# GPU Server Management

> Control remote GPU servers through natural language — run experiments, monitor training, sync code, analyze logs.

**Status**: ✅ Available
**Mode color**: `#00e5a0`
**Mode icon**: 🖥️

---

## What it does

This feature turns natural language instructions into actions on a remote GPU server over SSH. You describe what you want in Chinese or English; the agent figures out the right sequence of commands and tools, executes them, and reports back.

Typical workflows:
- "帮我把代码同步过去，然后用 conda 环境 torch 跑 train.py"
- "现在 GPU 利用率多少？有没有任务在跑？"
- "查一下最新的训练日志，loss 收敛了吗？"
- "创建一个新分支 exp/lr-search，修改 learning rate 为 1e-4，提交并同步"

---

## Infrastructure

```
Cloud Server (AImpire)
    │  Reverse SSH tunnel (autossh) OR direct SSH
    ▼
GPU Server
    ├── conda / virtualenv
    ├── tmux (persistent sessions)
    └── SLURM / screen / bare processes
```

The cloud server maintains a persistent SSH connection to the GPU server. The agent executes commands remotely and streams output back.

---

## Available Tools

| Tool | Description |
|---|---|
| `ssh_run` | Execute a shell command on the GPU server. Supports tmux for long-running tasks. |
| `check_task_status` | Check GPU utilization, tmux sessions, and running processes. |
| `sync_code` | Git commit locally and sync to GPU server via bundle. |
| `pull_results` | SCP files from the GPU server back to local. |
| `git_commit` | Commit local code changes. |
| `git_info` | Show branch, status, recent commits. |
| `git_branch` | Create and switch to a new branch. |
| `write_code` | Write or overwrite a local file. |
| `read_code` | Read a local file's contents. |
| `list_files` | List workspace files by glob pattern. |
| `analyze_log` | Parse training log text for metrics (loss, accuracy, etc.). |
| `analyze_remote_log` | Read and analyze a log file directly from the GPU server. |
| `tail_remote_log` | Get the last N lines of a remote log file. |
| `create_experiment` | Record a new experiment in the local database. |
| `update_experiment` | Update experiment status and results. |
| `experiment_history` | List recent experiments and their outcomes. |
| `scan_projects` | Discover all research projects on the GPU server. |
| `analyze_project` | Analyze a project's structure and generate `.agent.yaml`. |
| `get_project_config` | Read a project's `.agent.yaml` configuration. |
| `save_project_config` | Write a project's `.agent.yaml` to the GPU server. |

---

## Project Configuration (`.agent.yaml`)

Each project on the GPU server can have a `.agent.yaml` that tells the agent how to work with it:

```yaml
name: "my-experiment"
description: "Image classification with ResNet"
train_command: "python train.py --config configs/default.yaml"
conda_env: "torch"
tmux_session: "train-resnet"
log_path: "logs/train.log"
work_dir: "."                    # Relative to workspace/project_name/
key_metrics: ["train_loss", "val_acc"]
custom_patterns:                 # Regex patterns for log parsing
  - "Epoch (?P<epoch>\\d+)"
  - "loss=(?P<train_loss>[\\d.]+)"
```

On first use with a new project:
1. `scan_projects` — finds the project
2. `analyze_project` — reads code, infers structure
3. Agent generates `.agent.yaml` and saves it via `save_project_config`
4. Future sessions use the config automatically

---

## Setup

### SSH Key

The cloud server needs passwordless SSH access to the GPU server:

```bash
# On the cloud server:
ssh-keygen -t ed25519 -f ~/.ssh/gpu_key
ssh-copy-id -i ~/.ssh/gpu_key.pub user@GPU_SERVER_IP
```

### Direct SSH (GPU server has public IP)

```yaml
gpu_server:
  host: "YOUR_GPU_IP"
  port: 22
  username: "user"
  key_path: "~/.ssh/gpu_key"
  tunnel_port: null
```

### Reverse SSH Tunnel (GPU server behind NAT/firewall)

On the GPU server, create `/etc/systemd/system/ssh-tunnel.service`:

```ini
[Unit]
Description=Reverse SSH tunnel to AImpire cloud server
After=network.target

[Service]
User=GPU_USER
ExecStart=/usr/bin/autossh -M 0 -N \
  -R 2222:localhost:22 \
  -o ServerAliveInterval=60 \
  -o ServerAliveCountMax=3 \
  -i /home/GPU_USER/.ssh/id_ed25519 \
  CLOUD_USER@CLOUD_SERVER_IP
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now ssh-tunnel
```

Then in `config.yaml`:
```yaml
gpu_server:
  host: "localhost"
  tunnel_port: 2222
```

---

## Example Conversations

**Starting a training run:**
```
User: 把本地代码同步到服务器，然后在 tmux 里用 torch 环境跑 train.py
Agent: [sync_code] 已提交并同步代码
       [ssh_run] 已在 tmux session "train" 中启动训练
       训练已开始，tmux attach -t train 可查看实时输出
```

**Checking progress:**
```
User: 训练到哪了？
Agent: [check_task_status] GPU 利用率 94%，tmux session 运行中
       [tail_remote_log] 最新日志：Epoch 47/100, loss=0.312, val_acc=0.891
       训练进行顺利，val_acc 持续提升，预计还需约 2 小时
```

**Analyzing results:**
```
User: 训练完了，帮我分析一下结果
Agent: [analyze_remote_log] 解析 logs/train.log...
       最终结果：val_acc 0.923，best epoch 89
       对比实验历史：比上次提升 +2.1%
       建议：可以尝试降低 learning rate 进行 fine-tune
```

---

## Limitations

- Long-running commands (>60s) should use `ssh_run` with `use_tmux: true`
- Tool output is capped at 5000 chars per call; for large logs use `tail_remote_log`
- Code sync uses git bundle; the local workspace must be a git repo
- Session history is stored on the cloud server and persists across restarts
