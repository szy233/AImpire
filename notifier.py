"""
notifier — 消息推送

训练完成或出错时主动通知手机端：
- WebSocket 广播
- Telegram Bot（可选）
"""

from typing import Optional

from loguru import logger

from configs.config_manager import get_config


class Notifier:

    async def notify(self, message: str, level: str = "info"):
        """
        发送通知。

        Args:
            message: 通知内容
            level: info / warning / error / success
        """
        icon = {"info": "ℹ️", "warning": "⚠️", "error": "❌", "success": "✅"}.get(level, "📢")
        full_message = f"{icon} {message}"

        logger.info(f"通知: {full_message}")

        await self._try_websocket(full_message)
        await self._try_telegram(full_message)

    async def notify_experiment_started(self, exp_name: str, exp_id: int):
        await self.notify(f"实验 #{exp_id} [{exp_name}] 已启动", "info")

    async def notify_experiment_finished(self, exp_name: str, exp_id: int, summary: str):
        await self.notify(f"实验 #{exp_id} [{exp_name}] 已完成\n{summary}", "success")

    async def notify_experiment_failed(self, exp_name: str, exp_id: int, error: str):
        await self.notify(f"实验 #{exp_id} [{exp_name}] 失败\n原因: {error}", "error")

    # ===== WebSocket =====

    async def _try_websocket(self, message: str):
        try:
            from web.api_server import ws_manager
            await ws_manager.broadcast(message)
        except Exception:
            pass

    # ===== Telegram =====

    async def _try_telegram(self, message: str):
        cfg = get_config().notify
        if not cfg.telegram_bot_token or not cfg.telegram_chat_id:
            return

        try:
            import httpx
            url = f"https://api.telegram.org/bot{cfg.telegram_bot_token}/sendMessage"
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json={
                    "chat_id": cfg.telegram_chat_id,
                    "text": message,
                    "parse_mode": "HTML",
                })
                if resp.status_code == 200:
                    logger.debug("Telegram 通知发送成功")
                else:
                    logger.warning(f"Telegram 发送失败: {resp.text}")
        except ImportError:
            logger.debug("httpx 未安装，跳过 Telegram")
        except Exception as e:
            logger.warning(f"Telegram 通知失败: {e}")


# ===== 全局实例 =====
_notifier: Optional[Notifier] = None


def get_notifier() -> Notifier:
    global _notifier
    if _notifier is None:
        _notifier = Notifier()
    return _notifier
