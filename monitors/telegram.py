import logging
import os
from typing import Any

import requests


class TelegramNotifier:
    def __init__(self, config: dict[str, Any], state: Any | None = None) -> None:
        telegram_config = config.get("telegram", {}) or {}
        self.bot_token = (
            os.getenv("TELEGRAM_BOT_TOKEN")
            or os.getenv("MEERKAT_TELEGRAM_BOT_TOKEN")
            or telegram_config.get("bot_token")
        )
        self.chat_id = (
            os.getenv("TELEGRAM_CHAT_ID")
            or os.getenv("MEERKAT_TELEGRAM_CHAT_ID")
            or telegram_config.get("chat_id")
        )
        self.state = state
        self.enabled = bool(self.bot_token and self.chat_id)

        if not self.enabled:
            logging.warning(
                "Telegram is disabled. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID "
                "or telegram.bot_token/chat_id in config.yml."
            )
        else:
            logging.info(
                "Telegram enabled for chat_id=%s with bot token ending in ...%s",
                self.chat_id,
                str(self.bot_token)[-4:],
            )

    def send(self, text: str, force: bool = False) -> None:
        if not self.enabled:
            logging.info("Telegram disabled, would send: %s", text.replace("\n", " | "))
            return

        if not force and self.state and self.state.get("alerts.silenced", False):
            logging.info("Alerts silenced, skipped Telegram message: %s", text.replace("\n", " | "))
            return

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }

        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
        except requests.RequestException as exc:
            response_text = getattr(getattr(exc, "response", None), "text", "")
            logging.error("Telegram send failed: %s %s", exc, response_text)

    def send_alert(self, alert: Any, force: bool = False) -> None:
        labels = {
            "info": "ℹ️ INFO",
            "warning": "⚠️ WARNING",
            "critical": "🚨 CRITICAL",
            "emergency": "🆘 EMERGENCY",
        }
        heading = labels.get(alert.severity, alert.severity.upper())
        self.send(f"{heading}: {alert.title}\n\n{alert.body}", force=force)
