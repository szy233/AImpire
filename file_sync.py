"""
file_sync — 文件同步

通过反向隧道同步文件：
  云服务器 rsync/scp → localhost:{tunnel_port} → 算力服务器

提供：
- 代码推送（云服务器 → 算力服务器）
- 结果拉取（算力服务器 → 云服务器）
- 单文件传输
"""

import subprocess
from pathlib import Path
from typing import Optional, List

from loguru import logger

from configs.config_manager import get_config


class FileSync:
    @property
    def cfg(self):
        return get_config()

    @property
    def gpu(self):
        return self.cfg.gpu_server

    def _ssh_args(self) -> str:
        """
        构建 rsync/scp 的 SSH 参数。

        走反向隧道时连 localhost:{tunnel_port}，
        否则直连 host:port。
        """
        if self.gpu.tunnel_port:
            host = "localhost"
            port = self.gpu.tunnel_port
        else:
            host = self.gpu.host
            port = self.gpu.port

        args = f"ssh -p {port}"
        if self.gpu.resolved_key_path:
            args += f" -i {self.gpu.resolved_key_path}"
        args += " -o StrictHostKeyChecking=no -o ConnectTimeout=15"
        return args

    def _remote_str(self, path: str) -> str:
        """构建 user@host:path 格式"""
        if self.gpu.tunnel_port:
            host = "localhost"
        else:
            host = self.gpu.host
        return f"{self.gpu.username}@{host}:{path}"

    # ===== 代码推送 =====

    def push_code(self, local_dir: Optional[str] = None,
                  remote_dir: Optional[str] = None,
                  exclude: Optional[List[str]] = None) -> dict:
        """
        推送代码到算力服务器。

        默认排除大文件和临时文件，只同步代码。
        """
        local_dir = local_dir or self.cfg.local.workspace
        remote_dir = remote_dir or self.gpu.workspace

        default_excludes = [
            ".git", "__pycache__", "*.pyc", ".venv",
            "wandb", "outputs", "checkpoints",
            "*.pt", "*.pth", "*.bin",
            "data/", ".DS_Store",
        ]
        all_excludes = list(set((exclude or []) + default_excludes))

        cmd = self._build_rsync(
            src=f"{local_dir}/",
            dst=self._remote_str(remote_dir),
            excludes=all_excludes,
        )
        return self._run(cmd, "推送代码")

    # ===== 结果拉取 =====

    def pull_results(self, remote_patterns: Optional[List[str]] = None,
                     local_dir: Optional[str] = None) -> dict:
        """
        从算力服务器拉取训练结果。

        Args:
            remote_patterns: 指定文件/目录（相对于远程 workspace），
                            留空则拉取 outputs/ logs/ results/
            local_dir: 本地存放目录
        """
        local_dir = local_dir or self.cfg.local.results_dir
        Path(local_dir).mkdir(parents=True, exist_ok=True)

        if remote_patterns:
            last_result = None
            for pattern in remote_patterns:
                remote_full = f"{self.gpu.workspace}/{pattern}"
                cmd = self._build_rsync(
                    src=self._remote_str(remote_full),
                    dst=f"{local_dir}/",
                    excludes=[],
                )
                last_result = self._run(cmd, f"拉取 {pattern}")
            return last_result
        else:
            for d in ["outputs/", "logs/", "results/"]:
                remote_full = f"{self.gpu.workspace}/{d}"
                cmd = self._build_rsync(
                    src=self._remote_str(remote_full),
                    dst=f"{local_dir}/",
                    excludes=["*.pt", "*.pth", "*.bin"],
                )
                self._run(cmd, f"拉取 {d}")
            return {"success": True, "local_dir": local_dir}

    # ===== 单文件传输 =====

    def push_file(self, local_path: str, remote_path: str) -> dict:
        """推送单个文件到算力服务器"""
        if self.gpu.tunnel_port:
            port = self.gpu.tunnel_port
            host = "localhost"
        else:
            port = self.gpu.port
            host = self.gpu.host

        key_arg = ""
        if self.gpu.resolved_key_path:
            key_arg = f"-i {self.gpu.resolved_key_path}"

        cmd = (
            f"scp -P {port} {key_arg} "
            f"-o StrictHostKeyChecking=no "
            f"{local_path} {self.gpu.username}@{host}:{remote_path}"
        )
        return self._run(cmd, f"推送 {Path(local_path).name}")

    def pull_file(self, remote_path: str, local_path: str) -> dict:
        """从算力服务器拉取单个文件"""
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)

        if self.gpu.tunnel_port:
            port = self.gpu.tunnel_port
            host = "localhost"
        else:
            port = self.gpu.port
            host = self.gpu.host

        key_arg = ""
        if self.gpu.resolved_key_path:
            key_arg = f"-i {self.gpu.resolved_key_path}"

        cmd = (
            f"scp -P {port} {key_arg} "
            f"-o StrictHostKeyChecking=no "
            f"{self.gpu.username}@{host}:{remote_path} {local_path}"
        )
        return self._run(cmd, f"拉取 {Path(remote_path).name}")

    # ===== 内部方法 =====

    def _build_rsync(self, src: str, dst: str,
                     excludes: List[str], delete: bool = False) -> str:
        """构建 rsync 命令"""
        cmd = f"rsync -avz --progress -e '{self._ssh_args()}'"
        for ex in excludes:
            cmd += f" --exclude='{ex}'"
        if delete:
            cmd += " --delete"
        cmd += f" {src} {dst}"
        return cmd

    def _run(self, cmd: str, desc: str = "") -> dict:
        """执行 shell 命令"""
        logger.info(f"{desc}: {cmd[:120]}...")
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True,
                text=True, timeout=300,
            )
            success = result.returncode == 0
            if success:
                logger.info(f"{desc} 成功")
            else:
                logger.warning(f"{desc} 失败: {result.stderr[:200]}")
            return {
                "success": success,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "exit_code": result.returncode,
            }
        except subprocess.TimeoutExpired:
            logger.error(f"{desc} 超时 (300s)")
            return {
                "success": False,
                "stdout": "",
                "stderr": "命令执行超时",
                "exit_code": -1,
            }


# ===== 全局实例 =====
_syncer: Optional[FileSync] = None


def get_file_sync() -> FileSync:
    global _syncer
    if _syncer is None:
        _syncer = FileSync()
    return _syncer


# ===== CLI 测试 =====

if __name__ == "__main__":
    import sys
    from rich import print as rprint
    from configs.config_manager import load_config

    load_config()
    syncer = FileSync()

    rprint(f"[green]FileSync 已初始化[/green]")
    if syncer.gpu.tunnel_port:
        rprint(f"  模式: 反向隧道 (localhost:{syncer.gpu.tunnel_port})")
    else:
        rprint(f"  模式: 直连 ({syncer.gpu.host}:{syncer.gpu.port})")
    rprint(f"  本地工作区: {syncer.cfg.local.workspace}")
    rprint(f"  远程工作区: {syncer.gpu.workspace}")

    if "--test" in sys.argv:
        rprint("\n[yellow]测试推送...[/yellow]")
        result = syncer.push_code()
        if result.get("success"):
            rprint("[green]✓ 代码推送成功[/green]")
        else:
            rprint(f"[red]✗ 推送失败: {result.get('stderr', '')[:200]}[/red]")
    else:
        rprint("\n用法:")
        rprint("  python -m tools.file_sync --test   # 测试推送")
