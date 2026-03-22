# AImpire

> AI-powered natural language control center — one mobile interface to rule everything.

**English** | [中文](#中文说明)

---

## What is AImpire?

AImpire is a mobile-first platform that lets you control complex systems through natural language conversation. Instead of learning CLI tools, dashboards, or APIs for every system you manage, you talk to an AI agent that understands your intent and executes the right actions.

The interface is a single PWA chat app (installable on iOS/Android). The backend is a Claude-powered agent that routes your message to the appropriate feature module, executes tools, and streams results back in real time.

```
┌─────────────────────────────────────────────────┐
│              Mobile PWA (iOS / Android)          │
│          Natural language · Streaming            │
│  ┌───────────────────────────────────────────┐  │
│  │  Mode: [GPU Server] [PC] [Web] [Data] …   │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────┬───────────────────────┘
                          │  HTTPS + Bearer Token
                          ▼
┌─────────────────────────────────────────────────┐
│              Cloud Server (FastAPI)              │
│                                                  │
│   Claude API (streaming) · Multi-session         │
│   ┌────────────────────────────────────────┐    │
│   │  Feature Router                        │    │
│   │  gpu_server │ pc_control │ web │ …     │    │
│   └────────────────────────────────────────┘    │
│   Core Agent: tool use · history · persistence   │
└──────┬───────────────────────┬───────────────────┘
       │ SSH                   │ Future protocols
       ▼                       ▼
┌─────────────┐       ┌─────────────────┐
│  GPU Server │       │  Your PC / APIs │
└─────────────┘       └─────────────────┘
```

---

## Features

| Mode | Status | Description |
|---|---|---|
| 🖥️ **GPU Server** | ✅ Available | Manage remote GPU servers — run experiments, monitor jobs, sync code, analyze logs |
| 💻 **PC Control** | 🔜 Coming soon | Control your local PC — automate workflows, manage files, run scripts |
| 🌐 **Web Automation** | 🔜 Coming soon | Browser automation — data scraping, form filling, web interactions |
| 📊 **Data Analysis** | 🔜 Coming soon | Analyze datasets — generate charts, run statistics, export reports |

Each feature is an independent module. You can enable only what you need, or build your own.

---

## Quick Start

### 1. Clone

```bash
git clone https://github.com/szy233/AImpire.git
cd AImpire
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure

```bash
cp configs/config.example.yaml configs/config.yaml
```

Fill in `configs/config.yaml` — at minimum you need:

```yaml
claude:
  api_key: "sk-ant-..."        # Your Anthropic API key

server:
  auth_token: "your-token"     # Secret for mobile auth (anything you choose)
```

See [Config Reference](#config-reference) for all options.

### 4. Generate VAPID keys (Web Push notifications)

```bash
python setup_vapid.py
```

Copy the output into `config.yaml` under `server.vapid_public_key` and `server.vapid_private_key`.

### 5. Generate PWA icons

```bash
python generate_icons.py
```

### 6. Start the server

```bash
uvicorn web.api_server:app --host 0.0.0.0 --port 8000
```

### 7. Open on your phone

Navigate to `http://YOUR_SERVER_IP:8000` in Safari / Chrome, then:
- **iOS**: Share → Add to Home Screen (full PWA experience)
- **Android**: Menu → Install App

On first launch, enter your `auth_token` in the settings dialog.

---

## Project Structure

```
AImpire/
├── features/                   # Feature modules (one per capability)
│   ├── gpu_server/             # ✅ GPU server management
│   │   └── README.md           # Feature docs, tools, setup
│   ├── pc_control/             # 🔜 PC control (planned)
│   ├── web_automation/         # 🔜 Web automation (planned)
│   └── data_analysis/          # 🔜 Data analysis (planned)
│
├── core/                       # Shared infrastructure
│   ├── agent.py                # Core agent: Claude API, tool dispatch, history
│   ├── state_manager.py        # Experiment/task state (SQLite)
│   └── project_manager.py      # Project config (.agent.yaml)
│
├── tools/                      # Tool implementations
│   ├── ssh_executor.py         # Remote SSH execution
│   ├── git_manager.py          # Git operations + code sync
│   ├── file_sync.py            # File transfer (SCP)
│   └── log_analyzer.py         # Training log parsing
│
├── web/                        # Frontend + API server
│   ├── chat.html               # Mobile PWA — single-page app
│   ├── api_server.py           # FastAPI: auth, streaming, push, sessions
│   ├── manifest.json           # PWA manifest
│   └── sw.js                   # Service Worker (cache + push events)
│
├── configs/
│   ├── config.example.yaml     # Template (copy → config.yaml)
│   └── config_manager.py       # Pydantic config loader
│
├── setup_vapid.py              # VAPID key generator
├── generate_icons.py           # PWA icon generator
└── requirements.txt
```

---

## Adding a New Feature

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide. The short version:

1. Create `features/<your_feature>/`
2. Implement tools in `core/agent.py` (or a new agent subclass)
3. Add the mode entry to the `MODES` array in `web/chat.html`
4. Set `available: true` when ready

---

## Config Reference

```yaml
# ===== GPU Server =====
gpu_server:
  host: "localhost"             # "localhost" for tunnel mode, or direct IP
  port: 22
  username: "your_user"
  key_path: "~/.ssh/id_ed25519"
  password: null                # Leave null to use SSH key
  tunnel_port: 2222             # Reverse tunnel port; null = direct SSH
  workspace: "/home/user/projects"
  conda_env: null               # Conda env to activate

# ===== Local / Cloud Server =====
local:
  workspace: "./workspace"
  results_dir: "./results"
  db_path: "./data/state.db"

# ===== Claude API =====
claude:
  api_key: "sk-ant-xxxxx"
  model: "claude-sonnet-4-20250514"
  max_tokens: 4096

# ===== Web Server =====
server:
  host: "0.0.0.0"
  port: 8000
  auth_token: "your-secret-token"
  vapid_public_key: ""          # From setup_vapid.py
  vapid_private_key: ""         # From setup_vapid.py

# ===== Notifications (optional) =====
notify:
  telegram_bot_token: null
  telegram_chat_id: null
```

---

## Reverse SSH Tunnel (for GPU servers behind NAT)

On the GPU server:

```bash
autossh -M 0 -N -R 2222:localhost:22 \
  -o ServerAliveInterval=60 \
  YOUR_USER@YOUR_CLOUD_SERVER_IP
```

Or as a systemd service — see [features/gpu_server/README.md](features/gpu_server/README.md).

Then in `config.yaml`:
```yaml
gpu_server:
  host: "localhost"
  tunnel_port: 2222
```

---

## Systemd Service

```ini
[Unit]
Description=AImpire
After=network.target

[Service]
User=YOUR_USER
WorkingDirectory=/path/to/AImpire
ExecStart=/path/to/venv/bin/uvicorn web.api_server:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

---

## License

MIT

---

## 中文说明

AImpire 是一个移动端优先的 AI 控制平台。通过自然语言与 Claude 对话，即可管理 GPU 服务器、操控本地 PC、执行自动化任务——无需记忆命令行语法，一个聊天界面搞定一切。

当前已实现的功能模块：**GPU 服务器管理**（运行训练、监控任务、同步代码、分析日志）。

更多模块（PC 操控、Web 自动化、数据分析）正在开发中。各模块相互独立，可按需启用，也可自行扩展。

详见各功能目录下的 README：[features/gpu_server/README.md](features/gpu_server/README.md)
