# AImpire — Mobile GPU Server Agent

**English** | [中文](#中文说明)

An AI-powered, mobile-first web app for managing GPU servers via natural language. Chat with Claude to run experiments, monitor jobs, sync files, and manage your GPU cluster — all from your phone.

---

## Architecture

```
iPhone (PWA)
    │  HTTPS + Bearer token
    ▼
Cloud Server  ─── FastAPI + Claude (Anthropic API)
    │              Streaming chat · Multi-session · Web Push
    │  Reverse SSH Tunnel (autossh)
    ▼
GPU Server
    └── SSH executor · conda · SLURM / screen / tmux
```

---

## Features

- **Streaming chat** — token-by-token responses with abort support
- **Multi-project / multi-session** — organize conversations by project
- **Tool use** — Claude can run shell commands, sync files, read logs, manage git, and more
- **PWA + Web Push** — installable on iOS/Android; receive push notifications when jobs finish
- **Reverse SSH tunnel** — works even when the GPU server is behind NAT / firewall
- **Mobile-first UI** — dark theme, safe-area aware, bottom-sheet modals

---

## Prerequisites

- Python 3.10+
- An [Anthropic API key](https://console.anthropic.com/)
- A cloud server with a public IP (the FastAPI server runs here)
- A GPU server with SSH access (can be behind NAT via reverse tunnel)

---

## Quick Start

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/AImpire.git
cd AImpire
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Copy and edit the config

```bash
cp configs/config.example.yaml configs/config.yaml
```

Edit `configs/config.yaml` and fill in:

| Field | Description |
|---|---|
| `gpu_server.username` | Your GPU server username |
| `gpu_server.host` | GPU server host (use `localhost` for tunnel mode) |
| `gpu_server.tunnel_port` | Reverse tunnel port (e.g. `2222`), or `null` for direct SSH |
| `gpu_server.key_path` | Path to your SSH private key |
| `gpu_server.workspace` | Working directory on the GPU server |
| `gpu_server.conda_env` | Conda environment name (optional) |
| `claude.api_key` | Your Anthropic API key (`sk-ant-...`) |
| `server.auth_token` | A secret token for authenticating the mobile client |
| `server.vapid_public_key` | VAPID public key for Web Push (see step 5) |
| `server.vapid_private_key` | VAPID private key for Web Push (see step 5) |

### 4. Generate VAPID keys (for Web Push notifications)

```bash
python setup_vapid.py
```

Copy the output keys into `configs/config.yaml` under `server.vapid_public_key` and `server.vapid_private_key`.

### 5. Generate PWA icons

```bash
python generate_icons.py
```

### 6. Start the server

```bash
uvicorn api_server:app --host 0.0.0.0 --port 8000
```

Or with auto-reload during development:

```bash
uvicorn api_server:app --host 0.0.0.0 --port 8000 --reload
```

### 7. Open the app

Navigate to `http://YOUR_SERVER_IP:8000` in your browser (or add to home screen as a PWA).

On first launch, a settings dialog will appear — enter the `auth_token` you set in `config.yaml`.

---

## Systemd Service

Create `/etc/systemd/system/aimpire.service`:

```ini
[Unit]
Description=AImpire GPU Agent
After=network.target

[Service]
User=YOUR_USER
WorkingDirectory=/path/to/AImpire
ExecStart=/path/to/venv/bin/uvicorn api_server:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable aimpire
sudo systemctl start aimpire
```

---

## Reverse SSH Tunnel Setup

On the GPU server, install `autossh` and run:

```bash
autossh -M 0 -N -R 2222:localhost:22 \
  -o ServerAliveInterval=60 \
  -o ServerAliveCountMax=3 \
  YOUR_USER@YOUR_CLOUD_SERVER_IP
```

Or as a systemd service on the GPU server (`/etc/systemd/system/ssh-tunnel.service`):

```ini
[Unit]
Description=Reverse SSH tunnel to cloud server
After=network.target

[Service]
User=YOUR_GPU_USER
ExecStart=/usr/bin/autossh -M 0 -N \
  -R 2222:localhost:22 \
  -o ServerAliveInterval=60 \
  -o ServerAliveCountMax=3 \
  -i /home/YOUR_GPU_USER/.ssh/id_ed25519 \
  YOUR_USER@YOUR_CLOUD_SERVER_IP
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then in `configs/config.yaml`, set:

```yaml
gpu_server:
  host: "localhost"
  tunnel_port: 2222
```

---

## Config Reference

```yaml
# ===== GPU Server =====
gpu_server:
  host: "localhost"          # Use "localhost" for tunnel mode, or direct IP
  port: 22                   # SSH port on the GPU server
  username: "your_user"      # SSH username
  key_path: "~/.ssh/id_ed25519"  # SSH private key path
  password: null             # SSH password (leave null to use key)
  tunnel_port: 2222          # Reverse tunnel local port; null = direct SSH
  workspace: "/home/user/projects"  # Default working directory
  conda_env: null            # Conda env to activate (null = skip)

# ===== Local / Cloud Server =====
local:
  workspace: "./workspace"   # Local git repo root
  results_dir: "./results"   # Where pulled results are stored
  db_path: "./data/state.db" # SQLite database path

# ===== Claude API =====
claude:
  api_key: "sk-ant-xxxxx"
  model: "claude-sonnet-4-20250514"
  max_tokens: 4096

# ===== Web Server =====
server:
  host: "0.0.0.0"
  port: 8000
  auth_token: "your-secret-token"  # Shared secret for mobile auth
  vapid_public_key: ""       # Generated by setup_vapid.py
  vapid_private_key: ""      # Generated by setup_vapid.py

# ===== Notifications (optional) =====
notify:
  telegram_bot_token: null
  telegram_chat_id: null
```

---

## Agent Tool Capabilities

Claude has access to the following tools when chatting:

| Tool | Description |
|---|---|
| `run_command` | Execute shell commands on the GPU server |
| `read_file` | Read file contents from the GPU server |
| `write_file` | Write or overwrite files on the GPU server |
| `list_directory` | List directory contents |
| `sync_files` | Sync files between local and GPU server |
| `git_status` | Check git status of a repository |
| `git_commit` | Stage and commit changes |
| `git_pull` / `git_push` | Pull/push from remote |
| `get_gpu_status` | Query GPU utilization (nvidia-smi) |
| `tail_log` | Stream the last N lines of a log file |
| `analyze_log` | Summarize training logs (loss, metrics) |
| `list_projects` | List managed projects |
| `create_project` | Create a new project |
| `get_project_state` | Get current project status |

---

## Contributing

Pull requests are welcome. For major changes, please open an issue first to discuss what you would like to change.

1. Fork the repo
2. Create your feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -m 'Add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a Pull Request

---

## License

MIT License — see [LICENSE](LICENSE) for details.

---

## 中文说明

AImpire 是一个移动端优先的 AI GPU 服务器管理 Web 应用。通过自然语言与 Claude 对话，即可在手机上运行实验、监控任务、同步文件、管理 GPU 集群。

### 快速开始

```bash
git clone https://github.com/YOUR_USERNAME/AImpire.git
cd AImpire
pip install -r requirements.txt
cp configs/config.example.yaml configs/config.yaml
# 编辑 config.yaml，填写 GPU 服务器信息和 Anthropic API Key
python setup_vapid.py       # 生成 VAPID 推送密钥，粘贴到 config.yaml
python generate_icons.py    # 生成 PWA 图标
uvicorn api_server:app --host 0.0.0.0 --port 8000
```

浏览器打开 `http://你的服务器IP:8000`，首次启动时会弹出设置界面，填入 `auth_token` 即可连接。

### 功能特点

- 流式对话，逐 token 输出
- 多项目 / 多会话管理
- Claude 工具调用：执行命令、读写文件、分析日志、管理 Git
- PWA + Web Push 推送通知
- 反向 SSH 隧道支持（GPU 服务器无需公网 IP）
- 移动端深色主题 UI，适配 iOS 安全区域

### 许可证

MIT
