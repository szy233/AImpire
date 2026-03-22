"""
ssh_executor — SSH 远程命令执行

通过反向隧道连接算力服务器：
  算力服务器 --autossh--> 云服务器:2222
  本模块连接 localhost:2222 即等于连到算力服务器

提供：
- 单次命令执行（run）
- tmux 持久化执行（断连不丢任务）
- GPU 状态检查
- 隧道健康检测
- 自动重连
"""

import time
from pathlib import Path
from typing import Optional

import paramiko
from loguru import logger

from configs.config_manager import get_config


class SSHExecutor:
    def __init__(self):
        self._client: Optional[paramiko.SSHClient] = None

    @property
    def cfg(self):
        return get_config().gpu_server

    # ===== 连接管理 =====

    def connect(self, retries: int = 3) -> paramiko.SSHClient:
        """
        通过反向隧道连接算力服务器。

        有 tunnel_port 就连 localhost:2222，
        没有就直连 host:port（兼容模式）。
        失败会重试，给隧道重建留时间。
        """
        if self._client and self._client.get_transport() and self._client.get_transport().is_active():
            return self._client

        if self.cfg.tunnel_port:
            ssh_host = "localhost"
            ssh_port = self.cfg.tunnel_port
        else:
            ssh_host = self.cfg.host
            ssh_port = self.cfg.port

        last_error = None
        for attempt in range(1, retries + 1):
            try:
                logger.info(f"SSH 连接 → {self.cfg.username}@{ssh_host}:{ssh_port} (第 {attempt} 次)")
                client = paramiko.SSHClient()
                client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                connect_kwargs = {
                    "hostname": ssh_host,
                    "port": ssh_port,
                    "username": self.cfg.username,
                    "timeout": 15,
                }

                if self.cfg.password:
                    connect_kwargs["password"] = self.cfg.password
                elif self.cfg.resolved_key_path:
                    key_path = self.cfg.resolved_key_path
                    if not key_path.exists():
                        raise FileNotFoundError(f"SSH 密钥不存在: {key_path}")
                    connect_kwargs["key_filename"] = str(key_path)

                client.connect(**connect_kwargs)
                # 开启 TCP keepalive，防止长时间闲置后连接被防火墙静默断掉
                transport = client.get_transport()
                if transport:
                    transport.set_keepalive(60)
                self._client = client
                logger.info("SSH 连接成功")
                return client

            except Exception as e:
                last_error = e
                logger.warning(f"连接失败 (第 {attempt} 次): {e}")
                if attempt < retries:
                    wait = attempt * 5
                    logger.info(f"等待 {wait}s 后重试...")
                    time.sleep(wait)

        raise ConnectionError(
            f"SSH 连接失败（已重试 {retries} 次）: {last_error}\n"
            f"请检查算力服务器上隧道服务: sudo systemctl status gpu-tunnel"
        )

    def close(self):
        """关闭连接"""
        if self._client:
            self._client.close()
            self._client = None
            logger.info("SSH 连接已关闭")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()

    # ===== 命令执行 =====

    def run(self, command: str, timeout: int = 60) -> dict:
        """
        执行命令并等待完成。连接失效时自动重连一次。

        Returns:
            {"stdout": str, "stderr": str, "exit_code": int}
        """
        for attempt in range(2):
            try:
                client = self.connect()
                logger.info(f"执行: {command[:100]}...")
                stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
                exit_code = stdout.channel.recv_exit_status()
                result = {
                    "stdout": stdout.read().decode("utf-8", errors="replace"),
                    "stderr": stderr.read().decode("utf-8", errors="replace"),
                    "exit_code": exit_code,
                }
                if exit_code != 0:
                    logger.warning(f"退出码 {exit_code}: {result['stderr'][:200]}")
                else:
                    logger.info(f"成功, 输出 {len(result['stdout'])} 字符")
                return result
            except (paramiko.SSHException, EOFError, ConnectionResetError, OSError) as e:
                if attempt == 0:
                    logger.warning(f"SSH 命令失败，重连后重试: {e}")
                    self._client = None  # 强制重连
                else:
                    raise

    # ===== tmux 持久化任务 =====

    def run_in_tmux(self, command: str, session_name: str = "train") -> dict:
        """
        在 tmux 中启动任务，隧道断了任务也不丢。

        会先 kill 同名旧会话再创建新的。
        """
        conda_prefix = ""
        if self.cfg.conda_env:
            conda_prefix = f"conda activate {self.cfg.conda_env} && "

        self.run(f"tmux kill-session -t {session_name} 2>/dev/null || true", timeout=10)

        full_cmd = f'tmux new-session -d -s {session_name} "{conda_prefix}{command}"'
        result = self.run(full_cmd, timeout=10)

        if result["exit_code"] == 0:
            logger.info(f"tmux '{session_name}' 已启动")
        return result

    def check_tmux(self, session_name: str = "train") -> dict:
        """
        检查 tmux 会话状态 + 最近输出。

        Returns:
            {"running": bool, "output": str}
        """
        result = self.run(
            f"tmux has-session -t {session_name} 2>/dev/null && echo ALIVE || echo DEAD",
            timeout=10,
        )
        is_running = "ALIVE" in result["stdout"]

        output = ""
        if is_running:
            cap = self.run(f"tmux capture-pane -t {session_name} -p | tail -50", timeout=10)
            output = cap["stdout"]

        return {"running": is_running, "output": output}

    # ===== 隧道健康检查 =====

    def check_tunnel(self) -> dict:
        """
        检查反向隧道是否健康。

        Returns:
            {"alive": bool, "latency_ms": float, "hostname": str, "message": str}
        """
        start = time.time()
        try:
            result = self.run("echo TUNNEL_OK && hostname", timeout=10)
            latency = (time.time() - start) * 1000
            alive = result["exit_code"] == 0 and "TUNNEL_OK" in result["stdout"]
            return {
                "alive": alive,
                "latency_ms": round(latency, 1),
                "hostname": result["stdout"].replace("TUNNEL_OK\n", "").strip(),
                "message": "隧道正常" if alive else "隧道异常",
            }
        except Exception as e:
            return {
                "alive": False,
                "latency_ms": -1,
                "hostname": "",
                "message": f"隧道不通: {e}",
            }

    # ===== 常用快捷检查 =====

    def check_gpu(self) -> dict:
        """nvidia-smi 查看 GPU 状态"""
        return self.run(
            "nvidia-smi --query-gpu=index,name,utilization.gpu,memory.used,memory.total "
            "--format=csv,noheader",
            timeout=15,
        )

    def check_processes(self) -> dict:
        """查看正在运行的训练进程"""
        return self.run("ps aux | grep -E 'python|train' | grep -v grep", timeout=10)


# ===== 全局单例 =====
_executor: Optional[SSHExecutor] = None


def get_ssh_executor() -> SSHExecutor:
    global _executor
    if _executor is None:
        _executor = SSHExecutor()
    return _executor


# ===== CLI 测试 =====

if __name__ == "__main__":
    import sys
    from rich import print as rprint
    from configs.config_manager import load_config

    load_config()
    executor = SSHExecutor()

    if "--test" in sys.argv:
        try:
            rprint("[yellow]1. 检查隧道...[/yellow]")
            tunnel = executor.check_tunnel()
            if tunnel["alive"]:
                rprint(f"[green]✓ 隧道正常[/green] (延迟 {tunnel['latency_ms']}ms, 主机: {tunnel['hostname']})")
            else:
                rprint(f"[red]✗ 隧道不通: {tunnel['message']}[/red]")
                rprint("  请检查算力服务器: sudo systemctl status gpu-tunnel")
                sys.exit(1)

            rprint("\n[yellow]2. 测试命令执行...[/yellow]")
            result = executor.run("whoami && pwd && date")
            if result["exit_code"] == 0:
                rprint(f"[green]✓ 命令执行成功[/green]")
                rprint(f"  {result['stdout'].strip()}")

            rprint("\n[yellow]3. 检查 GPU...[/yellow]")
            gpu = executor.check_gpu()
            if gpu["exit_code"] == 0:
                rprint(f"[green]✓ GPU:[/green]\n{gpu['stdout']}")
            else:
                rprint("[yellow]⚠ nvidia-smi 不可用[/yellow]")

        except Exception as e:
            rprint(f"[red]✗ 失败: {e}[/red]")
        finally:
            executor.close()
    else:
        rprint("用法: python -m tools.ssh_executor --test")
