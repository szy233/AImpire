"""
git_manager — Git 版本管理 + 远程同步

云服务器上管理本地 Git 仓库，通过 git bundle 同步到算力服务器：
  Agent 生成代码 → write_file → commit → bundle → scp → 算力服务器 pull

用 bundle 而不是 git push/pull，因为算力服务器不能直接连回云服务器。

提供：
- 文件读写（供 Agent 调用）
- 提交与分支管理
- 远程同步（bundle + scp + 远程 pull）
- 查看历史与回滚
"""

import subprocess
from pathlib import Path
from typing import Optional, List

from loguru import logger

from configs.config_manager import get_config


class GitManager:
    def __init__(self, repo_path: Optional[str] = None):
        self.repo_path = Path(repo_path or get_config().local.workspace).resolve()

    def _run_git(self, *args, check: bool = True) -> dict:
        """执行 git 命令"""
        cmd = ["git", "-C", str(self.repo_path)] + list(args)
        logger.debug(f"git {' '.join(args)}")

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        output = {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "exit_code": result.returncode,
            "success": result.returncode == 0,
        }

        if not output["success"] and check:
            logger.warning(f"git 失败: {output['stderr']}")
        return output

    # ===== 初始化 =====

    def init(self) -> dict:
        """初始化本地 Git 仓库（已存在则跳过）"""
        self.repo_path.mkdir(parents=True, exist_ok=True)

        if (self.repo_path / ".git").exists():
            logger.info(f"仓库已存在: {self.repo_path}")
            return {"success": True, "message": "仓库已存在"}

        result = self._run_git("init")
        if result["success"]:
            gitignore = self.repo_path / ".gitignore"
            gitignore.write_text(
                "__pycache__/\n*.pyc\n.venv/\nwandb/\n"
                "*.pt\n*.pth\n*.bin\ncheckpoints/\n"
                "data/\n.DS_Store\noutputs/\n"
            )
            self._run_git("add", ".gitignore")
            self._run_git("commit", "-m", "初始化: 添加 .gitignore")
        return result

    def setup_gpu_repo(self) -> dict:
        """
        在算力服务器上初始化 Git 仓库并同步当前代码。

        通过 bundle 方式：本地打包 → scp 传过去 → 远程解包。
        算力服务器不需要能连回云服务器。
        """
        from tools.ssh_executor import get_ssh_executor
        from tools.file_sync import get_file_sync

        ssh = get_ssh_executor()
        syncer = get_file_sync()
        cfg = get_config()
        ws = cfg.gpu_server.workspace

        # 检查算力服务器上是否已有 .git
        check = ssh.run(f"test -d {ws}/.git && echo EXISTS || echo NOPE")

        if "EXISTS" in check["stdout"]:
            logger.info("算力服务器已有 Git 仓库，直接同步")
            return self.sync_to_gpu()

        # 确保远程目录存在
        ssh.run(f"mkdir -p {ws}")

        # 本地打包 bundle
        bundle_local = "/tmp/repo.bundle"
        bundle_remote = "/tmp/repo.bundle"
        self._run_git("bundle", "create", bundle_local, "--all")
        logger.info("Git bundle 已创建")

        # 传到算力服务器
        syncer.push_file(bundle_local, bundle_remote)

        # 在已有目录中初始化 git 并从 bundle 拉取
        ssh.run(f"cd {ws} && git init", timeout=15)
        pull = ssh.run(f"cd {ws} && git fetch {bundle_remote} && git merge FETCH_HEAD --no-edit", timeout=30)

        # 清理
        ssh.run(f"rm -f {bundle_remote}")
        Path(bundle_local).unlink(missing_ok=True)

        if pull["exit_code"] == 0:
            logger.info("算力服务器仓库初始化完成")
            return {"success": True, "message": "算力服务器仓库已初始化"}
        else:
            logger.warning(f"初始化失败: {pull['stderr']}")
            return {"success": False, "message": pull["stderr"]}

    # ===== 远程同步（核心）=====

    def sync_to_gpu(self, commit_msg: Optional[str] = None) -> dict:
        """
        一键同步代码到算力服务器。

        流程：本地 commit → 打 bundle → scp → 远程 fetch + merge

        这是 Agent 最常调用的方法。
        """
        from tools.ssh_executor import get_ssh_executor
        from tools.file_sync import get_file_sync

        # 1. 本地 commit（如果有改动）
        if commit_msg:
            self.commit_changes(commit_msg)

        # 2. 打包 bundle
        bundle_local = "/tmp/repo.bundle"
        bundle_remote = "/tmp/repo.bundle"

        bundle_result = self._run_git("bundle", "create", bundle_local, "--all")
        if not bundle_result["success"]:
            return {"success": False, "message": f"bundle 创建失败: {bundle_result['stderr']}"}

        # 3. scp 传到算力服务器
        syncer = get_file_sync()
        push_result = syncer.push_file(bundle_local, bundle_remote)
        if not push_result.get("success"):
            return {"success": False, "message": "bundle 传输失败"}

        # 4. 算力服务器 fetch + merge
        ssh = get_ssh_executor()
        ws = get_config().gpu_server.workspace

        pull_result = ssh.run(
            f"cd {ws} && git fetch {bundle_remote} && git merge FETCH_HEAD --no-edit",
            timeout=30,
        )

        # 5. 清理
        ssh.run(f"rm -f {bundle_remote}")
        Path(bundle_local).unlink(missing_ok=True)

        success = pull_result["exit_code"] == 0
        if success:
            logger.info("代码已同步到算力服务器")
        else:
            logger.warning(f"同步失败: {pull_result['stderr']}")

        return {
            "success": success,
            "message": "同步成功" if success else pull_result["stderr"],
        }

    # ===== 分支 =====

    def create_branch(self, name: str) -> dict:
        """创建并切换到新分支"""
        result = self._run_git("checkout", "-b", name, check=False)
        if not result["success"] and "already exists" in result["stderr"]:
            return self._run_git("checkout", name)
        return result

    def current_branch(self) -> str:
        """获取当前分支名"""
        return self._run_git("branch", "--show-current")["stdout"]

    def list_branches(self) -> List[str]:
        """列出所有分支"""
        result = self._run_git("branch", "--list")
        return [b.strip().lstrip("* ") for b in result["stdout"].split("\n") if b.strip()]

    # ===== 提交 =====

    def commit_changes(self, message: str, files: Optional[List[str]] = None) -> dict:
        """提交改动"""
        if files:
            for f in files:
                self._run_git("add", f)
        else:
            self._run_git("add", "-A")

        status = self._run_git("status", "--porcelain")
        if not status["stdout"]:
            return {"success": True, "message": "没有需要提交的改动"}

        return self._run_git("commit", "-m", message)

    # ===== 查看 =====

    def status(self) -> str:
        return self._run_git("status", "--short")["stdout"]

    def diff(self, staged: bool = False) -> str:
        args = ["diff", "--stat"]
        if staged:
            args.append("--cached")
        return self._run_git(*args)["stdout"]

    def log(self, n: int = 10) -> str:
        return self._run_git("log", f"-{n}", "--oneline", "--graph")["stdout"]

    def show_commit(self, commit_hash: str = "HEAD") -> str:
        return self._run_git("show", commit_hash, "--stat")["stdout"]

    # ===== 文件操作 =====

    def write_file(self, relative_path: str, content: str) -> Path:
        """写入文件到仓库"""
        file_path = self.repo_path / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        logger.info(f"写入: {relative_path} ({len(content)} 字符)")
        return file_path

    def read_file(self, relative_path: str) -> Optional[str]:
        """读取仓库中的文件"""
        file_path = self.repo_path / relative_path
        if not file_path.exists():
            return None
        return file_path.read_text(encoding="utf-8")

    def list_files(self, pattern: str = "**/*.py") -> List[str]:
        return [str(p.relative_to(self.repo_path)) for p in self.repo_path.glob(pattern)]

    # ===== 回滚 =====

    def rollback(self, commit_hash: str = "HEAD~1") -> dict:
        return self._run_git("reset", "--hard", commit_hash)


# ===== 全局实例 =====
_manager: Optional[GitManager] = None


def get_git_manager() -> GitManager:
    global _manager
    if _manager is None:
        _manager = GitManager()
    return _manager


# ===== CLI 测试 =====

if __name__ == "__main__":
    import sys
    from rich import print as rprint
    from configs.config_manager import load_config

    load_config()
    gm = GitManager()

    if "--init" in sys.argv:
        rprint("[yellow]1. 初始化本地仓库...[/yellow]")
        gm.init()
        rprint(f"[green]✓ 本地仓库: {gm.repo_path}[/green]")

        rprint("\n[yellow]2. 初始化算力服务器仓库...[/yellow]")
        result = gm.setup_gpu_repo()
        if result["success"]:
            rprint(f"[green]✓ {result['message']}[/green]")
        else:
            rprint(f"[red]✗ {result['message']}[/red]")

    elif "--sync" in sys.argv:
        rprint("[yellow]同步代码到算力服务器...[/yellow]")
        result = gm.sync_to_gpu("手动同步测试")
        if result["success"]:
            rprint("[green]✓ 同步成功[/green]")
        else:
            rprint(f"[red]✗ {result['message']}[/red]")

    elif "--test" in sys.argv:
        gm.init()
        rprint(f"[green]✓ Git 仓库: {gm.repo_path}[/green]")
        rprint(f"  当前分支: {gm.current_branch()}")
        rprint(f"  所有分支: {gm.list_branches()}")
        rprint(f"  最近提交:\n{gm.log(5)}")

    else:
        rprint("用法:")
        rprint("  python -m tools.git_manager --init   # 初始化本地+远程仓库")
        rprint("  python -m tools.git_manager --sync   # 同步代码到算力服务器")
        rprint("  python -m tools.git_manager --test   # 查看仓库状态")
