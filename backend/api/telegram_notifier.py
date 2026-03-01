"""
Telegram notification helper.

Sends a fire-and-forget message to a Telegram bot using the Bot API.
Credentials are read from Settings; if not configured the call is silently skipped.

Usage:
    asyncio.create_task(notify_simulation_start(agent_count=5, config_name="pavone"))
"""

import html
import logging
from typing import Optional

import httpx

from backend.config.settings import settings

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


async def _send(message: str) -> None:
    """Low-level async POST to Telegram Bot API."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if not token or not chat_id:
        logger.debug("Telegram not configured — skipping notification.")
        return

    url = _TELEGRAM_API.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": html.escape(message),
        "parse_mode": "HTML",
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            logger.info("Telegram notification sent.")
    except Exception as exc:  # never crash the main flow
        logger.warning("Telegram notification failed: %s", exc)


async def notify_simulation_start(
    config_name: Optional[str] = None,
    agent_count: Optional[int] = None,
) -> None:
    """Send a notification when a user starts the simulation."""
    parts = ["🤖 <b>Warehouse Swarm — simulazione avviata</b>"]

    if config_name:
        parts.append(f"📋 Config: <code>{html.escape(config_name)}</code>")
    if agent_count is not None:
        parts.append(f"👥 Agenti: {agent_count}")

    await _send("\n".join(parts))
