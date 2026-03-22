"""
project_manager — 多项目管理

管理算力服务器上的多个研究项目：
- 扫描发现项目
- 读写 .agent.yaml 配置
- 自动生成初始配置（让 Claude 分析项目结构）
- 项目列表和状态
"""

import json
import yaml
from pathlib import Path
from typing import Optional, List, Dict
from dataclasses import dataclass, field, asdict

from loguru import logger

from configs.config_manager import get_config
from tools.ssh_executor import get_ssh_executor


@dataclass
class ProjectConfig:
    """项目配置（对应 .agent.yaml）"""
    # 基本信息
    name: str = ""
    description: str = ""
    conda_env: Optional[str] = None

    # 训练
    train_command: str = ""
    work_dir: str = "."
    gpu_count: int = 1
    tmux_session: str = "train"

    # 可调参数
    config_file: Optional[str] = None
    config_format: str = "yaml"
    tunable_params: List[Dict] = field(default_factory=list)

    # 日志
    log_path: str = "logs/train.log"
    log_format: str = "text"
    custom_patterns: Dict[str, str] = field(default_factory=dict)
    key_metrics: List[str] = field(default_factory=list)
    finish_markers: List[str] = field(default_factory=lambda: ["training complete", "best model saved"])

    # 输出
    checkpoint_dir: str = "checkpoints/"
    results_dir: str = "results/"
    best_model: str = "checkpoints/best.pth"

    # 评估
    eval_command: Optional[str] = None
    eval_metrics_file: Optional[str] = None

    def to_yaml(self) -> str:
        """导出为 YAML 字符串"""
        data = {
            "project": {
                "name": self.name,
                "description": self.description,
                "conda_env": self.conda_env,
            },
            "train": {
                "command": self.train_command,
                "work_dir": self.work_dir,
                "gpu_count": self.gpu_count,
                "tmux_session": self.tmux_session,
            },
            "params": {
                "config_file": self.config_file,
                "format": self.config_format,
                "tunable": self.tunable_params,
            },
            "logging": {
                "path": self.log_path,
                "format": self.log_format,
                "patterns": self.custom_patterns,
                "key_metrics": self.key_metrics,
                "finish_markers": self.finish_markers,
            },
            "output": {
                "checkpoint_dir": self.checkpoint_dir,
                "results_dir": self.results_dir,
                "best_model": self.best_model,
            },
        }
        if self.eval_command:
            data["eval"] = {
                "command": self.eval_command,
                "metrics_file": self.eval_metrics_file,
            }
        return yaml.dump(data, allow_unicode=True, default_flow_style=False, sort_keys=False)

    @classmethod
    def from_yaml(cls, text: str) -> "ProjectConfig":
        """从 YAML 文本解析"""
        data = yaml.safe_load(text)
        if not data:
            return cls()

        project = data.get("project", {})
        train = data.get("train", {})
        params = data.get("params", {})
        logging_cfg = data.get("logging", {})
        output = data.get("output", {})
        eval_cfg = data.get("eval", {})

        return cls(
            name=project.get("name", ""),
            description=project.get("description", ""),
            conda_env=project.get("conda_env"),
            train_command=train.get("command", ""),
            work_dir=train.get("work_dir", "."),
            gpu_count=train.get("gpu_count", 1),
            tmux_session=train.get("tmux_session", "train"),
            config_file=params.get("config_file"),
            config_format=params.get("format", "yaml"),
            tunable_params=params.get("tunable", []),
            log_path=logging_cfg.get("path", "logs/train.log"),
            log_format=logging_cfg.get("format", "text"),
            custom_patterns=logging_cfg.get("patterns", {}),
            key_metrics=logging_cfg.get("key_metrics", []),
            finish_markers=logging_cfg.get("finish_markers", ["training complete", "best model saved"]),
            checkpoint_dir=output.get("checkpoint_dir", "checkpoints/"),
            results_dir=output.get("results_dir", "results/"),
            best_model=output.get("best_model", "checkpoints/best.pth"),
            eval_command=eval_cfg.get("command"),
            eval_metrics_file=eval_cfg.get("metrics_file"),
        )


class ProjectManager:
    def __init__(self):
        self.ssh = get_ssh_executor()
        self.base_path = get_config().gpu_server.workspace

    # ===== 项目发现 =====

    def scan_projects(self) -> List[Dict]:
        """
        扫描算力服务器 workspace 下的所有项目。

        识别规则：包含 .py 文件或 .agent.yaml 的一级子目录。
        """
        result = self.ssh.run(
            f"ls -d {self.base_path}/*/ 2>/dev/null | head -50",
            timeout=10,
        )

        if result["exit_code"] != 0:
            return []

        projects = []
        dirs = [d.strip().rstrip("/") for d in result["stdout"].split("\n") if d.strip()]

        for dir_path in dirs:
            dir_name = dir_path.split("/")[-1]

            # 跳过隐藏目录和常见非项目目录
            if dir_name.startswith(".") or dir_name in ("data", "datasets", "__pycache__", ".git"):
                continue

            # 检查是否有 .agent.yaml 或 .py 文件
            check = self.ssh.run(
                f"(test -f {dir_path}/.agent.yaml && echo CONFIGURED) || "
                f"(ls {dir_path}/*.py 2>/dev/null | head -1 | grep -q . && echo PYTHON) || "
                f"echo SKIP",
                timeout=5,
            )

            status = check["stdout"].strip()

            if status != "SKIP":
                projects.append({
                    "name": dir_name,
                    "path": dir_path,
                    "configured": status == "CONFIGURED",
                })
            else:
                # 当前目录没有 .py 也没有 .agent.yaml，往下找一级子项目
                sub = self.ssh.run(
                    f"for d in {dir_path}/*/; do "
                    f"  n=$(basename $d); "
                    f"  [ \"$n\" = '__pycache__' ] && continue; "
                    f"  (test -f ${{d}}.agent.yaml && echo \"CONFIGURED:$n:$d\") || "
                    f"  (ls ${{d}}*.py 2>/dev/null | head -1 | grep -q . && echo \"PYTHON:$n:$d\"); "
                    f"done",
                    timeout=8,
                )
                for line in sub["stdout"].splitlines():
                    line = line.strip().rstrip("/")
                    if not line:
                        continue
                    parts = line.split(":", 2)
                    if len(parts) != 3:
                        continue
                    sub_status, sub_name, sub_path = parts
                    projects.append({
                        "name": f"{dir_name}/{sub_name}",
                        "path": sub_path.rstrip("/"),
                        "configured": sub_status == "CONFIGURED",
                    })

        logger.info(f"发现 {len(projects)} 个项目")
        return projects

    # ===== 读写配置 =====

    def get_project_config(self, project_name: str) -> Optional[ProjectConfig]:
        """读取项目的 .agent.yaml"""
        config_path = f"{self.base_path}/{project_name}/.agent.yaml"
        result = self.ssh.run(f"cat {config_path} 2>/dev/null", timeout=10)

        if result["exit_code"] != 0 or not result["stdout"].strip():
            return None

        try:
            return ProjectConfig.from_yaml(result["stdout"])
        except Exception as e:
            logger.warning(f"解析 {project_name}/.agent.yaml 失败: {e}")
            return None

    def save_project_config(self, project_name: str, config: ProjectConfig) -> bool:
        """保存项目的 .agent.yaml"""
        config_path = f"{self.base_path}/{project_name}/.agent.yaml"
        yaml_content = config.to_yaml()

        # 通过 SSH 写入文件
        # 用 heredoc 避免转义问题
        escaped = yaml_content.replace("'", "'\\''")
        result = self.ssh.run(
            f"cat > {config_path} << 'AGENT_YAML_EOF'\n{yaml_content}AGENT_YAML_EOF",
            timeout=10,
        )

        if result["exit_code"] == 0:
            logger.info(f"已保存 {project_name}/.agent.yaml")
            return True
        else:
            logger.warning(f"保存失败: {result['stderr']}")
            return False

    # ===== 项目分析（让 Claude 生成配置）=====

    def analyze_project(self, project_name: str) -> Dict:
        """
        分析项目结构，收集信息供 Claude 生成 .agent.yaml。

        返回项目结构信息字典。
        """
        project_path = f"{self.base_path}/{project_name}"

        info = {"name": project_name, "path": project_path}

        # 文件结构
        tree = self.ssh.run(
            f"find {project_path} -maxdepth 2 -type f "
            f"\\( -name '*.py' -o -name '*.yaml' -o -name '*.yml' "
            f"-o -name '*.json' -o -name '*.sh' -o -name '*.cfg' "
            f"-o -name 'requirements.txt' -o -name '*.toml' \\) "
            f"| head -50",
            timeout=10,
        )
        info["files"] = tree["stdout"]

        # README
        readme = self.ssh.run(f"cat {project_path}/README.md 2>/dev/null | head -50", timeout=5)
        if readme["exit_code"] == 0 and readme["stdout"]:
            info["readme"] = readme["stdout"]

        # 配置文件内容（如果有）
        for cfg_name in ["config.yaml", "configs/base.yaml", "config.json", "configs/default.yaml"]:
            cfg = self.ssh.run(f"cat {project_path}/{cfg_name} 2>/dev/null | head -80", timeout=5)
            if cfg["exit_code"] == 0 and cfg["stdout"]:
                info["config_file"] = cfg_name
                info["config_content"] = cfg["stdout"]
                break

        # 训练脚本内容（前 30 行看 argparse）
        for script in ["train.py", "main.py", "run.py"]:
            s = self.ssh.run(f"head -50 {project_path}/{script} 2>/dev/null", timeout=5)
            if s["exit_code"] == 0 and s["stdout"]:
                info["train_script"] = script
                info["train_script_head"] = s["stdout"]
                break

        # requirements.txt
        req = self.ssh.run(f"cat {project_path}/requirements.txt 2>/dev/null | head -30", timeout=5)
        if req["exit_code"] == 0 and req["stdout"]:
            info["requirements"] = req["stdout"]

        # 日志目录
        logs = self.ssh.run(
            f"find {project_path} -maxdepth 2 -type f -name '*.log' | head -5",
            timeout=5,
        )
        if logs["stdout"]:
            info["log_files"] = logs["stdout"]

        return info

    # ===== 项目摘要 =====

    def project_summary(self) -> str:
        """生成所有项目的摘要文本"""
        projects = self.scan_projects()
        if not projects:
            return "未发现任何项目"

        lines = [f"📁 项目列表 ({len(projects)} 个):\n"]
        for p in projects:
            icon = "✅" if p["configured"] else "⚠️"
            status = "已配置" if p["configured"] else "未配置"
            lines.append(f"  {icon} {p['name']} [{status}]")

        return "\n".join(lines)


# ===== 全局实例 =====
_pm: Optional[ProjectManager] = None


def get_project_manager() -> ProjectManager:
    global _pm
    if _pm is None:
        _pm = ProjectManager()
    return _pm


# ===== CLI 测试 =====

if __name__ == "__main__":
    import sys
    from rich import print as rprint
    from configs.config_manager import load_config

    load_config()
    pm = ProjectManager()

    if "--scan" in sys.argv:
        rprint("[yellow]扫描项目...[/yellow]\n")
        rprint(pm.project_summary())

    elif "--analyze" in sys.argv and len(sys.argv) > sys.argv.index("--analyze") + 1:
        project_name = sys.argv[sys.argv.index("--analyze") + 1]
        rprint(f"[yellow]分析项目: {project_name}[/yellow]\n")
        info = pm.analyze_project(project_name)
        for k, v in info.items():
            rprint(f"[dim]{k}:[/dim]")
            rprint(f"  {str(v)[:200]}\n")

    else:
        rprint("用法:")
        rprint("  python -m core.project_manager --scan              # 扫描所有项目")
        rprint("  python -m core.project_manager --analyze <项目名>  # 分析项目结构")
