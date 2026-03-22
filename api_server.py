"""
api_server — FastAPI HTTP 入口

手机端通过这个接口与 Agent 交互：
- POST /chat    发送指令，获取回复
- GET  /status  查看当前状态
- GET  /experiments  查看实验列表
- WebSocket /ws  实时通信
"""

import json as json_module
from typing import Optional
from pathlib import Path

import base64, json as _json
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.backends import default_backend

from fastapi import FastAPI, HTTPException, Depends, Header, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.responses import StreamingResponse as FastAPIStreamingResponse
from pydantic import BaseModel

from loguru import logger

# VAPID keys are loaded from config at startup
VAPID_PUBLIC_KEY = ""
VAPID_PRIVATE_KEY = ""

from configs.config_manager import load_config, get_config
from core.agent import get_agent
from core.state_manager import get_state_manager


# ===== 初始化 =====

app = FastAPI(title="GPU Agent", version="0.1.0")

# 存储 push 订阅（内存，服务重启丢失）
push_subscriptions: dict = {}  # session_id -> subscription_info


async def send_push(session_id: str, title: str, body: str):
    """发送 Web Push 通知（手动实现，不依赖 pywebpush）
    需要: pip install pyjwt httpx
    """
    sub = push_subscriptions.get(session_id)
    if not sub:
        return
    try:
        import httpx, time, jwt as _jwt  # pip install pyjwt httpx
        endpoint = sub["endpoint"]
        audience = "/".join(endpoint.split("/")[:3])

        # 构建 VAPID JWT
        claims = {
            "aud": audience,
            "exp": int(time.time()) + 3600,
            "sub": "mailto:agent@aimpire.local"
        }

        # 从存储的私钥重建 EC key
        priv_bytes = base64.urlsafe_b64decode(VAPID_PRIVATE_KEY + "==")
        priv_int = int.from_bytes(priv_bytes, 'big')
        private_key = ec.derive_private_key(priv_int, ec.SECP256R1(), default_backend())

        # 用 ES256 签名
        token = _jwt.encode(claims, private_key, algorithm="ES256")

        headers = {
            "Authorization": f"vapid t={token}, k={VAPID_PUBLIC_KEY}",
            "Content-Type": "application/json",
            "TTL": "60",
        }

        # 加密 payload（简化：只发送明文，真正加密需要 ece 库）
        # 这里先用不加密的方式（部分浏览器接受）
        payload = _json.dumps({"title": title, "body": body[:120]})

        async with httpx.AsyncClient() as client:
            await client.post(endpoint, content=payload.encode(), headers=headers, timeout=10)
    except Exception as e:
        logger.warning(f"Push 发送失败: {e}")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    load_config()
    global VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY
    cfg_s = get_config().server
    VAPID_PUBLIC_KEY = cfg_s.vapid_public_key
    VAPID_PRIVATE_KEY = cfg_s.vapid_private_key
    logger.info("GPU Agent 启动")


# ===== 鉴权 =====

async def verify_token(authorization: Optional[str] = Header(None)):
    """简单 token 验证"""
    cfg = get_config()
    expected = cfg.server.auth_token
    if expected and expected != "changeme":
        token = ""
        if authorization:
            token = authorization.replace("Bearer ", "")
        if token != expected:
            raise HTTPException(status_code=401, detail="未授权")


# ===== 数据模型 =====

class ChatRequest(BaseModel):
    message: str
    project_id: Optional[str] = None
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    reply: str
    status: str = "ok"


# ===== 核心路由 =====

@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(verify_token)])
async def chat(req: ChatRequest):
    """
    发送自然语言指令，Agent 处理后返回结果。

    手机端调用示例：
        POST /chat
        {"message": "把 lr 改成 0.001 重新跑实验", "project_id": "diffusion"}
    """
    try:
        agent = get_agent()
        reply = await agent.process_message(req.message, project_id=req.project_id, session_id=req.session_id)
        return ChatResponse(reply=reply)
    except Exception as e:
        logger.error(f"Agent 处理失败: {e}")
        return ChatResponse(reply=f"处理出错: {str(e)}", status="error")


@app.post("/chat/stream", dependencies=[Depends(verify_token)])
async def chat_stream(req: ChatRequest):
    """流式对话接口，返回 SSE 事件流。"""
    agent = get_agent()

    async def generate():
        import asyncio
        try:
            gen = agent.process_message_stream(
                req.message,
                project_id=req.project_id,
                session_id=req.session_id,
            )
            while True:
                try:
                    event = await asyncio.wait_for(gen.__anext__(), timeout=8.0)
                    yield f"data: {json_module.dumps(event, ensure_ascii=False)}\n\n"
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # 心跳，防止 iOS Safari 断连
        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield f"data: {json_module.dumps({'type': 'error', 'content': str(e)})}\n\n"
        finally:
            yield "data: [DONE]\n\n"
            # 发送 push 通知（如果有订阅）
            if req.session_id and req.session_id in push_subscriptions:
                import asyncio
                asyncio.create_task(send_push(
                    req.session_id,
                    "AImpire",
                    f"{'@' + req.project_id + ' ' if req.project_id else ''}Claude 回复了"
                ))

    return FastAPIStreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


class PushSubscription(BaseModel):
    session_id: str
    subscription: dict  # {endpoint, keys: {p256dh, auth}}

@app.post("/push/subscribe", dependencies=[Depends(verify_token)])
async def push_subscribe(sub: PushSubscription):
    push_subscriptions[sub.session_id] = sub.subscription
    return {"status": "ok"}

@app.delete("/push/subscribe/{session_id}", dependencies=[Depends(verify_token)])
async def push_unsubscribe(session_id: str):
    push_subscriptions.pop(session_id, None)
    return {"status": "ok"}

@app.get("/push/vapid-key")
async def get_vapid_key():
    return {"publicKey": VAPID_PUBLIC_KEY}


@app.delete("/sessions/{session_id}", dependencies=[Depends(verify_token)])
async def delete_session(session_id: str):
    """清除指定会话的服务端对话历史"""
    agent = get_agent()
    agent.clear_session(session_id)
    return {"status": "ok"}


@app.get("/projects", dependencies=[Depends(verify_token)])
async def list_projects():
    """列出所有项目"""
    from core.project_manager import get_project_manager
    pm = get_project_manager()
    return {"projects": pm.scan_projects()}


@app.get("/status", dependencies=[Depends(verify_token)])
async def status():
    """查看系统状态"""
    state = get_state_manager()
    return {
        "summary": state.summary_text(),
        "running": state.get_running(),
    }


@app.get("/experiments", dependencies=[Depends(verify_token)])
async def experiments(limit: int = 10):
    """查看实验列表"""
    state = get_state_manager()
    return {"experiments": state.get_latest(limit)}


@app.get("/experiments/{exp_id}", dependencies=[Depends(verify_token)])
async def experiment_detail(exp_id: int):
    """查看单个实验详情"""
    state = get_state_manager()
    exp = state.get_experiment(exp_id)
    if not exp:
        raise HTTPException(status_code=404, detail="实验不存在")
    actions = state.get_actions(exp_id)
    return {"experiment": exp, "actions": actions}


# ===== WebSocket =====

class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        self.active.remove(ws)

    async def broadcast(self, message: str):
        for ws in self.active:
            try:
                await ws.send_text(message)
            except Exception:
                pass


ws_manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """
    WebSocket 实时通信。

    手机端发文本消息 → Agent 处理 → 返回结果。
    """
    await ws_manager.connect(ws)
    try:
        while True:
            data = await ws.receive_text()
            agent = get_agent()
            try:
                reply = await agent.process_message(data)
                await ws.send_text(reply)
            except Exception as e:
                await ws.send_text(f"出错: {str(e)}")
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


# ===== 聊天页面 =====

@app.get("/", response_class=HTMLResponse)
async def chat_page():
    """手机端聊天页面"""
    html_path = Path(__file__).parent / "chat.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


# ===== 健康检查 =====

@app.get("/health")
async def health():
    return {"status": "ok", "service": "gpu-agent"}


# ===== PWA 静态文件 =====

@app.get("/manifest.json")
async def manifest():
    return FileResponse(str(Path(__file__).parent / "web" / "manifest.json"), media_type="application/manifest+json")

@app.get("/sw.js")
async def service_worker():
    return FileResponse(str(Path(__file__).parent / "web" / "sw.js"), media_type="application/javascript")

@app.get("/icon-192.png")
async def icon_192():
    return FileResponse(str(Path(__file__).parent / "web" / "icon-192.png"), media_type="image/png")

@app.get("/icon-512.png")
async def icon_512():
    return FileResponse(str(Path(__file__).parent / "web" / "icon-512.png"), media_type="image/png")


# ===== 启动入口 =====

if __name__ == "__main__":
    import uvicorn
    load_config()
    cfg = get_config()
    uvicorn.run(
        "web.api_server:app",
        host=cfg.server.host,
        port=cfg.server.port,
        reload=True,
    )
