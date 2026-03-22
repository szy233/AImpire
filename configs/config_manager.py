"""
config_manager — 配置加载与验证

从 YAML 文件加载配置，用 Pydantic 做校验，全局单例访问。
"""

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


# ===== 配置模型 =====

class GPUServerConfig(BaseModel):
    host: str = "localhost"            # 直连模式用，隧道模式可留 localhost
    port: int = 22
    username: str
    key_path: Optional[str] = "~/.ssh/id_ed25519"
    password: Optional[str] = None
    tunnel_port: Optional[int] = 2222  # 反向隧道端口，设为 None 则直连
    workspace: str = "/home/user/projects"
    conda_env: Optional[str] = None

    @property
    def resolved_key_path(self) -> Optional[Path]:
        if self.key_path:
            return Path(self.key_path).expanduser()
        return None


class LocalConfig(BaseModel):
    workspace: str = "./workspace"
    results_dir: str = "./results"
    db_path: str = "./data/state.db"

    def ensure_dirs(self):
        """确保本地目录存在"""
        for p in [self.workspace, self.results_dir]:
            Path(p).mkdir(parents=True, exist_ok=True)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)


class ClaudeConfig(BaseModel):
    api_key: str
    model: str = "claude-sonnet-4-20250514"
    max_tokens: int = 8192


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    auth_token: str = "changeme"
    vapid_public_key: str = ""
    vapid_private_key: str = ""


class NotifyConfig(BaseModel):
    telegram_bot_token: Optional[str] = None
    telegram_chat_id: Optional[str] = None


class AppConfig(BaseModel):
    gpu_server: GPUServerConfig
    local: LocalConfig = Field(default_factory=LocalConfig)
    claude: ClaudeConfig
    server: ServerConfig = Field(default_factory=ServerConfig)
    notify: NotifyConfig = Field(default_factory=NotifyConfig)


# ===== 加载逻辑 =====

_config: Optional[AppConfig] = None


def load_config(config_path: str = "configs/config.yaml") -> AppConfig:
    """从 YAML 文件加载配置"""
    global _config

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(
            f"配置文件不存在: {path}\n"
            f"请先复制模板: cp configs/config.example.yaml configs/config.yaml"
        )

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    _config = AppConfig(**raw)
    _config.local.ensure_dirs()
    return _config


def get_config() -> AppConfig:
    """获取全局配置（必须先调用 load_config）"""
    if _config is None:
        raise RuntimeError("配置未加载，请先调用 load_config()")
    return _config


# ===== CLI 测试 =====

if __name__ == "__main__":
    from rich import print as rprint

    try:
        cfg = load_config()
        rprint("[green]✓ 配置加载成功[/green]")
        rprint(f"  GPU 用户:   {cfg.gpu_server.username}@{cfg.gpu_server.host}")
        if cfg.gpu_server.tunnel_port:
            rprint(f"  连接模式:   反向隧道 (localhost:{cfg.gpu_server.tunnel_port})")
        else:
            rprint(f"  连接模式:   直连 ({cfg.gpu_server.host}:{cfg.gpu_server.port})")
        rprint(f"  SSH 密钥:   {cfg.gpu_server.resolved_key_path}")
        rprint(f"  远程工作区: {cfg.gpu_server.workspace}")
        rprint(f"  本地工作区: {cfg.local.workspace}")
        rprint(f"  Claude 模型: {cfg.claude.model}")
        rprint(f"  Web 端口:   {cfg.server.port}")
    except FileNotFoundError as e:
        rprint(f"[red]✗ {e}[/red]")
    except Exception as e:
        rprint(f"[red]✗ 配置校验失败: {e}[/red]")
