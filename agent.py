"""
agent_core — Agent 调度核心

接收自然语言指令 → 调用 Claude API 理解意图 → 调度工具执行。

工具链：
- ssh_executor: 远程命令
- git_manager: 代码管理 + 同步
- file_sync: 结果拉取
- log_analyzer: 日志解析
- state_manager: 实验状态
"""

import asyncio
import json
from pathlib import Path
from typing import Optional, Dict, Any, List

from loguru import logger

HISTORY_DIR = Path("/home/agent/gpu-agent/data/sessions")

from configs.config_manager import get_config
from core.state_manager import get_state_manager, TaskStatus
from tools.ssh_executor import get_ssh_executor
from tools.file_sync import get_file_sync
from tools.git_manager import get_git_manager
from tools.log_analyzer import get_log_analyzer


# ===== 工具定义（Claude API tool schema）=====

TOOL_DEFINITIONS = [
    {
        "name": "ssh_run",
        "description": "在算力服务器上执行命令。用于查看进程、检查 GPU、运行脚本等。",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
                "use_tmux": {"type": "boolean", "description": "是否在 tmux 中持久运行（长时间任务用）", "default": False},
                "session_name": {"type": "string", "description": "tmux 会话名", "default": "train"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "check_task_status",
        "description": "检查算力服务器上的任务状态：GPU 使用率、tmux 会话、运行中的进程。",
        "input_schema": {
            "type": "object",
            "properties": {
                "session_name": {"type": "string", "default": "train"},
            },
        },
    },
    {
        "name": "sync_code",
        "description": "同步代码到算力服务器。先 git commit，再通过 bundle 传输到算力服务器。",
        "input_schema": {
            "type": "object",
            "properties": {
                "commit_message": {"type": "string", "description": "git 提交信息"},
            },
        },
    },
    {
        "name": "pull_results",
        "description": "从算力服务器拉取训练结果和日志。",
        "input_schema": {
            "type": "object",
            "properties": {
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要拉取的文件路径列表（相对于远程 workspace），留空则拉取默认目录",
                },
            },
        },
    },
    {
        "name": "git_commit",
        "description": "提交代码改动到本地 Git。",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "提交信息"},
                "files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要提交的文件列表，留空则提交所有改动",
                },
            },
            "required": ["message"],
        },
    },
    {
        "name": "git_info",
        "description": "查看 Git 仓库状态：当前分支、改动、最近提交。",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "git_branch",
        "description": "创建并切换到新的 Git 分支。",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "分支名"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "write_code",
        "description": "写入或修改代码文件到本地工作区。用于生成训练脚本、修改配置等。",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "文件路径（相对于工作区）"},
                "content": {"type": "string", "description": "文件内容"},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "read_code",
        "description": "读取工作区中的代码文件内容。",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "文件路径（相对于工作区）"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "list_files",
        "description": "列出工作区中的文件。",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "glob 匹配模式", "default": "**/*.py"},
            },
        },
    },
    {
        "name": "analyze_log",
        "description": "解析训练日志文本，提取 loss、accuracy 等关键指标并生成摘要。",
        "input_schema": {
            "type": "object",
            "properties": {
                "log_text": {"type": "string", "description": "日志文本内容"},
            },
            "required": ["log_text"],
        },
    },
    {
        "name": "analyze_remote_log",
        "description": "直接从算力服务器读取并解析训练日志。",
        "input_schema": {
            "type": "object",
            "properties": {
                "log_path": {"type": "string", "description": "日志文件路径（相对于远程 workspace 或绝对路径）"},
            },
            "required": ["log_path"],
        },
    },
    {
        "name": "tail_remote_log",
        "description": "查看算力服务器上日志文件的最后 N 行（快速看进度）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "log_path": {"type": "string", "description": "日志文件路径"},
                "lines": {"type": "integer", "description": "行数", "default": 50},
            },
            "required": ["log_path"],
        },
    },
    {
        "name": "create_experiment",
        "description": "在数据库中记录一个新实验。",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "实验名称"},
                "params": {"type": "object", "description": "实验参数"},
                "command": {"type": "string", "description": "执行命令"},
                "branch": {"type": "string", "description": "Git 分支名", "default": ""},
            },
            "required": ["name"],
        },
    },
    {
        "name": "update_experiment",
        "description": "更新实验状态。",
        "input_schema": {
            "type": "object",
            "properties": {
                "exp_id": {"type": "integer", "description": "实验 ID"},
                "status": {"type": "string", "enum": ["pending", "running", "success", "failed", "cancelled"]},
                "result_summary": {"type": "string", "description": "结果摘要", "default": ""},
            },
            "required": ["exp_id", "status"],
        },
    },
    {
        "name": "experiment_history",
        "description": "查看实验历史和当前运行状态。",
        "input_schema": {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "description": "查看最近几条", "default": 5},
            },
        },
    },
    {
        "name": "scan_projects",
        "description": "扫描算力服务器上的所有研究项目。",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "analyze_project",
        "description": "分析项目结构，收集文件、配置、脚本等信息，用于生成或更新 .agent.yaml。",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "项目目录名"},
            },
            "required": ["project_name"],
        },
    },
    {
        "name": "get_project_config",
        "description": "读取项目的 .agent.yaml 配置。",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "项目目录名"},
            },
            "required": ["project_name"],
        },
    },
    {
        "name": "save_project_config",
        "description": "保存项目的 .agent.yaml 配置到算力服务器。传入完整的 YAML 内容。",
        "input_schema": {
            "type": "object",
            "properties": {
                "project_name": {"type": "string", "description": "项目目录名"},
                "yaml_content": {"type": "string", "description": ".agent.yaml 的完整 YAML 内容"},
            },
            "required": ["project_name", "yaml_content"],
        },
    },
]

# ===== 系统提示词 =====

SYSTEM_PROMPT = """你是 AI 研究助理，管理远程 GPU 服务器上的机器学习实验。

职责：理解自然语言指令 → 管理代码 → 启动/监控训练 → 分析结果 → 管理多项目

多项目：
- 注意消息中的 [当前项目] 标记
- 首次接管：scan_projects → analyze_project → save_project_config
- 有配置后用 train_command / log_path / conda_env / tmux_session

标准流程：
- 改代码：write_code → git_commit → sync_code → ssh_run
- 长任务必须用 tmux（use_tmux: true）
- 看进度：check_task_status / tail_remote_log
- 完成后：analyze_remote_log → update_experiment

规则：危险操作先确认；Git 信息要清晰；结果给具体数字和建议；中文回复，简洁。
"""

# ===== Prompt Caching =====
# 系统提示词和工具定义不变，加 cache_control 让 Anthropic 缓存（命中后成本降 90%）
_SYSTEM_WITH_CACHE = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]

_TOOLS_WITH_CACHE = list(TOOL_DEFINITIONS)
_TOOLS_WITH_CACHE[-1] = {**_TOOLS_WITH_CACHE[-1], "cache_control": {"type": "ephemeral"}}


# API 各 content block 类型允许的字段
_BLOCK_ALLOWED_KEYS = {
    "text":        {"type", "text"},
    "tool_use":    {"type", "id", "name", "input"},
    "tool_result": {"type", "tool_use_id", "content"},
    "image":       {"type", "source"},
}

MAX_HISTORY_MSGS = 40        # 最多保留最近 40 条消息（约 20 轮对话）
TOOL_RESULT_MAX_LEN = 2000  # tool_result 内容截断阈值

# 触发注入状态上下文的关键词（其余消息跳过 summary_text() 节省 token）
_STATE_KEYWORDS = {"状态", "gpu", "任务", "实验", "进程", "查看", "检查", "日志", "训练", "跑", "运行"}


def _should_inject_state(msg: str, is_new_session: bool) -> bool:
    """新会话或消息含状态关键词时才注入 state.summary_text()。"""
    if is_new_session:
        return True
    lm = msg.lower()
    return any(k in lm for k in _STATE_KEYWORDS)


def _trim_history(msgs: List[Dict]) -> List[Dict]:
    """截断过长的 tool_result，并只保留最近 MAX_HISTORY_MSGS 条。"""
    trimmed = []
    for m in msgs:
        content = m["content"]
        if isinstance(content, list):
            new_content = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    c = block.get("content", "")
                    if isinstance(c, str) and len(c) > TOOL_RESULT_MAX_LEN:
                        block = {**block, "content": c[:TOOL_RESULT_MAX_LEN] + "\n…[已截断]"}
                new_content.append(block)
            trimmed.append({"role": m["role"], "content": new_content})
        else:
            trimmed.append(m)
    # 只保留最近 N 条，但保证第一条是 user（API 要求）
    if len(trimmed) > MAX_HISTORY_MSGS:
        trimmed = trimmed[-MAX_HISTORY_MSGS:]
        # 确保从 user 消息开始
        while trimmed and trimmed[0]["role"] != "user":
            trimmed = trimmed[1:]
    return trimmed


def _clean_block(block: dict) -> dict:
    """去掉 SDK model_dump() 带出的内部字段，只保留 API 接受的字段。"""
    t = block.get("type", "")
    allowed = _BLOCK_ALLOWED_KEYS.get(t)
    if allowed:
        return {k: v for k, v in block.items() if k in allowed}
    return block


def _serialize_messages(msgs: List[Dict]) -> List[Dict]:
    """将包含 SDK 对象的 messages 序列化为纯 JSON-safe dict 列表。"""
    result = []
    for m in msgs:
        content = m["content"]
        if isinstance(content, list):
            serialized = []
            for block in content:
                if hasattr(block, "model_dump"):
                    serialized.append(_clean_block(block.model_dump()))
                elif isinstance(block, dict):
                    serialized.append(_clean_block(block))
                else:
                    serialized.append(str(block))
            result.append({"role": m["role"], "content": serialized})
        else:
            result.append({"role": m["role"], "content": content})
    return result


class Agent:
    def __init__(self):
        self._client = None
        # session_id -> full serialized messages list (includes tool_use/tool_result)
        self.session_histories: Dict[str, List[Dict]] = {}

    def _load_history(self, session_id: str) -> List[Dict]:
        path = HISTORY_DIR / f"{session_id}.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return []
        return []

    def _save_history(self, session_id: str, messages: List[Dict]):
        try:
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            path = HISTORY_DIR / f"{session_id}.json"
            path.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"保存会话历史失败: {e}")

    @property
    def client(self):
        """懒加载 Anthropic 客户端"""
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.AsyncAnthropic(api_key=get_config().claude.api_key)
            except ImportError:
                raise RuntimeError("请安装 anthropic: pip install anthropic")
        return self._client

    def clear_session(self, session_id: str):
        """清除指定会话的历史（内存 + 磁盘）"""
        self.session_histories.pop(session_id, None)
        path = HISTORY_DIR / f"{session_id}.json"
        if path.exists():
            path.unlink(missing_ok=True)

    async def process_message(
        self,
        user_message: str,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """
        处理用户消息，返回最终回复。

        Args:
            user_message: 用户指令
            project_id: 项目名（可选）
            session_id: 会话 ID（可选，有则维护多轮对话历史）
        """
        state = get_state_manager()
        context = state.summary_text()

        # 构建项目上下文
        project_context = ""
        if project_id:
            from core.project_manager import get_project_manager
            pm = get_project_manager()
            cfg = pm.get_project_config(project_id)
            if cfg:
                project_context = (
                    f"\n[当前项目] {project_id}\n"
                    f"  描述: {cfg.description}\n"
                    f"  训练命令: {cfg.train_command}\n"
                    f"  conda 环境: {cfg.conda_env}\n"
                    f"  tmux 会话: {cfg.tmux_session}\n"
                    f"  日志路径: {cfg.log_path}\n"
                    f"  关键指标: {', '.join(cfg.key_metrics) if cfg.key_metrics else '未配置'}\n"
                    f"  工作目录: {get_config().gpu_server.workspace}/{project_id}/{cfg.work_dir}\n"
                )
            else:
                project_context = f"\n[当前项目] {project_id}（未配置 .agent.yaml，可用 analyze_project 分析后生成）\n"

        # 懒加载磁盘历史
        if session_id and session_id not in self.session_histories:
            loaded = self._load_history(session_id)
            if loaded:
                self.session_histories[session_id] = loaded
        history = self.session_histories.get(session_id, []) if session_id else []
        messages = list(_trim_history(history))

        # 仅在新会话或含状态关键词时注入 summary（节省 token）
        is_new = not bool(history)
        if _should_inject_state(user_message, is_new):
            user_content = f"[当前状态]\n{context}{project_context}\n[用户指令]\n{user_message}"
        elif project_context:
            user_content = f"{project_context}\n[用户指令]\n{user_message}"
        else:
            user_content = user_message
        messages.append({"role": "user", "content": user_content})

        cfg = get_config().claude
        max_rounds = 20
        final_text = []
        loop = asyncio.get_event_loop()

        for round_num in range(max_rounds):
            logger.info(f"Agent 轮次 {round_num + 1}")

            response = await self.client.messages.create(
                model=cfg.model,
                max_tokens=cfg.max_tokens,
                system=_SYSTEM_WITH_CACHE,
                tools=_TOOLS_WITH_CACHE,
                messages=messages,
            )

            text_parts = []
            tool_uses = []

            for block in response.content:
                if block.type == "text":
                    text_parts.append(block.text)
                elif block.type == "tool_use":
                    tool_uses.append(block)

            final_text.extend(text_parts)

            # 将 SDK 对象序列化后追加（保证 messages 全为可 JSON 化的 dict）
            serialized_content = [
                _clean_block(b.model_dump()) if hasattr(b, "model_dump") else _clean_block(b) if isinstance(b, dict) else b
                for b in response.content
            ]
            messages.append({"role": "assistant", "content": serialized_content})

            if not tool_uses:
                break

            # 并行执行所有工具（在线程池中运行阻塞 IO）
            for t in tool_uses:
                logger.info(f"调用工具: {t.name}({json.dumps(t.input, ensure_ascii=False)[:100]})")
            raw_results = await asyncio.gather(*[
                loop.run_in_executor(None, self._execute_tool, t.name, t.input)
                for t in tool_uses
            ])

            tool_results = [
                {"type": "tool_result", "tool_use_id": t.id, "content": str(r)}
                for t, r in zip(tool_uses, raw_results)
            ]
            messages.append({"role": "user", "content": tool_results})

        final_response = "\n".join(final_text)

        # 只存文本对（与 Claude 客户端一致）
        if session_id is not None:
            hist = list(self.session_histories.get(session_id, []))
            hist.append({"role": "user", "content": user_message})
            hist.append({"role": "assistant", "content": final_response})
            self.session_histories[session_id] = hist
            self._save_history(session_id, hist)

        return final_response

    async def process_message_stream(
        self,
        user_message: str,
        project_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ):
        """流式处理用户消息，yield SSE event dicts。"""
        state = get_state_manager()
        context = state.summary_text()

        project_context = ""
        if project_id:
            from core.project_manager import get_project_manager
            pm = get_project_manager()
            cfg_proj = pm.get_project_config(project_id)
            if cfg_proj:
                project_context = (
                    f"\n[当前项目] {project_id}\n"
                    f"  描述: {cfg_proj.description}\n"
                    f"  训练命令: {cfg_proj.train_command}\n"
                    f"  conda 环境: {cfg_proj.conda_env}\n"
                    f"  tmux 会话: {cfg_proj.tmux_session}\n"
                    f"  日志路径: {cfg_proj.log_path}\n"
                    f"  关键指标: {', '.join(cfg_proj.key_metrics) if cfg_proj.key_metrics else '未配置'}\n"
                    f"  工作目录: {get_config().gpu_server.workspace}/{project_id}/{cfg_proj.work_dir}\n"
                )
            else:
                project_context = f"\n[当前项目] {project_id}（未配置 .agent.yaml）\n"

        if session_id and session_id not in self.session_histories:
            loaded = self._load_history(session_id)
            if loaded:
                self.session_histories[session_id] = loaded
        history = self.session_histories.get(session_id, []) if session_id else []
        messages = list(_trim_history(history))

        # 仅在新会话或含状态关键词时注入 summary（节省 token）
        is_new = not bool(history)
        if _should_inject_state(user_message, is_new):
            user_content = f"[当前状态]\n{context}{project_context}\n[用户指令]\n{user_message}"
        elif project_context:
            user_content = f"{project_context}\n[用户指令]\n{user_message}"
        else:
            user_content = user_message
        messages.append({"role": "user", "content": user_content})

        cfg = get_config().claude
        max_rounds = 20
        all_text_parts = []
        loop = asyncio.get_event_loop()

        for round_num in range(max_rounds):
            logger.info(f"Agent 流式轮次 {round_num + 1}")

            async with self.client.messages.stream(
                model=cfg.model,
                max_tokens=cfg.max_tokens,
                system=_SYSTEM_WITH_CACHE,
                tools=_TOOLS_WITH_CACHE,
                messages=messages,
            ) as stream:
                round_text = []
                async for text in stream.text_stream:
                    round_text.append(text)
                    yield {"type": "text", "delta": text}

                all_text_parts.extend(round_text)
                final_msg = await stream.get_final_message()

            tool_uses = [b for b in final_msg.content if b.type == "tool_use"]

            if not tool_uses:
                break

            # 先广播所有 tool_call 事件
            for t in tool_uses:
                logger.info(f"调用工具: {t.name}")
                yield {"type": "tool_call", "name": t.name, "input": json.dumps(t.input, ensure_ascii=False)[:120]}

            # 并行执行（在线程池中，不阻塞事件循环）
            raw_results = await asyncio.gather(*[
                loop.run_in_executor(None, self._execute_tool, t.name, t.input)
                for t in tool_uses
            ])

            tool_results = []
            for t, result in zip(tool_uses, raw_results):
                result_str = str(result)
                yield {"type": "tool_result", "name": t.name, "content": result_str[:300]}
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": t.id,
                    "content": result_str,
                })

            serialized_content = [
                _clean_block(b.model_dump()) if hasattr(b, "model_dump") else _clean_block(b) if isinstance(b, dict) else b
                for b in final_msg.content
            ]
            messages.append({"role": "assistant", "content": serialized_content})
            messages.append({"role": "user", "content": tool_results})

        final_response = "".join(all_text_parts)

        # 只存文本对，不存 tool_use/tool_result（与 Claude 客户端一致，历史轻量）
        if session_id is not None:
            hist = list(self.session_histories.get(session_id, []))
            hist.append({"role": "user", "content": user_message})
            hist.append({"role": "assistant", "content": final_response})
            self.session_histories[session_id] = hist
            self._save_history(session_id, hist)

        yield {"type": "done", "full_text": final_response}

    def _execute_tool(self, name: str, params: Dict[str, Any]) -> str:
        """执行工具调用"""
        try:
            # ----- SSH -----
            if name == "ssh_run":
                ssh = get_ssh_executor()
                if params.get("use_tmux"):
                    result = ssh.run_in_tmux(params["command"], params.get("session_name", "train"))
                else:
                    result = ssh.run(params["command"], timeout=params.get("timeout", 60))
                return json.dumps(result, ensure_ascii=False)

            elif name == "check_task_status":
                ssh = get_ssh_executor()
                tmux = ssh.check_tmux(params.get("session_name", "train"))
                gpu = ssh.check_gpu()
                procs = ssh.check_processes()
                return json.dumps({
                    "tmux_session": tmux,
                    "gpu_status": gpu["stdout"],
                    "running_processes": procs["stdout"],
                }, ensure_ascii=False)

            # ----- Git + 同步 -----
            elif name == "sync_code":
                gm = get_git_manager()
                result = gm.sync_to_gpu(params.get("commit_message"))
                return json.dumps(result, ensure_ascii=False)

            elif name == "git_commit":
                gm = get_git_manager()
                result = gm.commit_changes(params["message"], params.get("files"))
                return json.dumps(result, ensure_ascii=False)

            elif name == "git_info":
                gm = get_git_manager()
                return json.dumps({
                    "branch": gm.current_branch(),
                    "status": gm.status(),
                    "recent_commits": gm.log(5),
                }, ensure_ascii=False)

            elif name == "git_branch":
                gm = get_git_manager()
                result = gm.create_branch(params["name"])
                return json.dumps(result, ensure_ascii=False)

            # ----- 文件 -----
            elif name == "write_code":
                gm = get_git_manager()
                path = gm.write_file(params["file_path"], params["content"])
                return f"文件已写入: {path}"

            elif name == "read_code":
                gm = get_git_manager()
                content = gm.read_file(params["file_path"])
                if content is None:
                    return f"文件不存在: {params['file_path']}"
                return content[:5000]

            elif name == "list_files":
                gm = get_git_manager()
                files = gm.list_files(params.get("pattern", "**/*.py"))
                return json.dumps(files, ensure_ascii=False)

            # ----- 结果 -----
            elif name == "pull_results":
                syncer = get_file_sync()
                result = syncer.pull_results(remote_patterns=params.get("files"))
                return json.dumps(result, ensure_ascii=False)

            # ----- 日志分析 -----
            elif name == "analyze_log":
                analyzer = get_log_analyzer()
                summary = analyzer.parse_log_text(params["log_text"])
                return summary.to_text()

            elif name == "analyze_remote_log":
                analyzer = get_log_analyzer()
                summary = analyzer.parse_remote_log(params["log_path"])
                return summary.to_text()

            elif name == "tail_remote_log":
                analyzer = get_log_analyzer()
                return analyzer.tail_remote_log(params["log_path"], params.get("lines", 50))

            # ----- 实验管理 -----
            elif name == "create_experiment":
                state = get_state_manager()
                exp_id = state.create_experiment(
                    name=params["name"],
                    params=params.get("params"),
                    command=params.get("command", ""),
                    branch=params.get("branch", ""),
                )
                return f"实验 #{exp_id} 已创建"

            elif name == "update_experiment":
                state = get_state_manager()
                state.update_status(
                    params["exp_id"],
                    TaskStatus(params["status"]),
                    params.get("result_summary", ""),
                )
                return f"实验 #{params['exp_id']} 状态已更新为 {params['status']}"

            elif name == "experiment_history":
                state = get_state_manager()
                return state.summary_text()

            # ----- 项目管理 -----
            elif name == "scan_projects":
                from core.project_manager import get_project_manager
                pm = get_project_manager()
                return pm.project_summary()

            elif name == "analyze_project":
                from core.project_manager import get_project_manager
                pm = get_project_manager()
                info = pm.analyze_project(params["project_name"])
                return json.dumps(info, ensure_ascii=False)

            elif name == "get_project_config":
                from core.project_manager import get_project_manager
                pm = get_project_manager()
                cfg = pm.get_project_config(params["project_name"])
                if cfg:
                    return cfg.to_yaml()
                return f"项目 {params['project_name']} 尚未配置 .agent.yaml"

            elif name == "save_project_config":
                from core.project_manager import get_project_manager, ProjectConfig
                pm = get_project_manager()
                cfg = ProjectConfig.from_yaml(params["yaml_content"])
                success = pm.save_project_config(params["project_name"], cfg)
                return "配置已保存" if success else "保存失败"

            else:
                return f"未知工具: {name}"

        except Exception as e:
            logger.error(f"工具 {name} 执行失败: {e}")
            return f"执行失败: {str(e)}"


# ===== 全局实例 =====
_agent: Optional[Agent] = None


def get_agent() -> Agent:
    global _agent
    if _agent is None:
        _agent = Agent()
    return _agent


# ===== CLI 测试 =====

if __name__ == "__main__":
    import asyncio
    import sys
    from rich import print as rprint
    from configs.config_manager import load_config

    load_config()

    async def test():
        agent = get_agent()

        if len(sys.argv) > 1:
            msg = " ".join(sys.argv[1:])
        else:
            msg = "查看一下 GPU 状态和当前有没有在跑的任务"

        rprint(f"[yellow]发送: {msg}[/yellow]\n")
        reply = await agent.process_message(msg)
        rprint(f"[green]回复:[/green]\n{reply}")

    asyncio.run(test())
