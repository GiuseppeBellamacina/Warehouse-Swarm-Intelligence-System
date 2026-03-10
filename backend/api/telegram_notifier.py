"""
Telegram notification helper.

Sends fire-and-forget messages to a Telegram bot using the Bot API.
Credentials are read from Settings; if not configured calls are silently skipped.
"""

import logging
from typing import Optional

import httpx

from backend.config.settings import settings

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


async def _send(message: str) -> None:
    """Low-level async POST to Telegram Bot API (plain text, no parse_mode)."""
    token = settings.telegram_bot_token
    chat_id = settings.telegram_chat_id

    if not token or not chat_id:
        logger.debug("Telegram not configured — skipping notification.")
        return

    url = _TELEGRAM_API.format(token=token)
    payload = {
        "chat_id": chat_id,
        "text": message,
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
    user_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    scouts: Optional[int] = None,
    coordinators: Optional[int] = None,
    retrievers: Optional[int] = None,
    grid_size: Optional[int] = None,
    total_objects: Optional[int] = None,
    map_known: bool = False,
) -> None:
    """Notify when a user starts the simulation."""
    lines = ["\u25b6\ufe0f Warehouse Swarm \u2014 simulazione avviata"]

    if config_name:
        lines.append(f"Config: {config_name}")
    if grid_size is not None:
        lines.append(f"Griglia: {grid_size}\u00d7{grid_size}")
    if total_objects is not None:
        lines.append(f"Oggetti: {total_objects}")
    if agent_count is not None:
        parts = [f"{agent_count} agenti"]
        if scouts is not None and coordinators is not None and retrievers is not None:
            parts.append(f"({scouts}S {coordinators}C {retrievers}R)")
        lines.append("Agenti: " + " ".join(parts))
    if map_known:
        lines.append("Modalit\u00e0: mappa pre-nota")
    if user_ip:
        lines.append(f"IP: {user_ip}")
    if user_agent:
        lines.append(f"Client: {user_agent[:80]}")

    await _send("\n".join(lines))


async def notify_simulation_complete(
    config_name: Optional[str] = None,
    steps: Optional[int] = None,
    objects_retrieved: Optional[int] = None,
    total_objects: Optional[int] = None,
    elapsed_seconds: Optional[float] = None,
) -> None:
    """Notify when the simulation finishes naturally (all objects retrieved)."""
    lines = ["\u2705 Warehouse Swarm \u2014 simulazione completata"]

    if config_name:
        lines.append(f"Config: {config_name}")
    if steps is not None:
        lines.append(f"Steps: {steps}")
    if objects_retrieved is not None and total_objects is not None:
        lines.append(f"Oggetti: {objects_retrieved}/{total_objects}")
    if elapsed_seconds is not None:
        minutes, secs = divmod(int(elapsed_seconds), 60)
        if minutes:
            lines.append(f"Durata: {minutes}m {secs}s")
        else:
            lines.append(f"Durata: {secs}s")

    await _send("\n".join(lines))


async def notify_simulation_stopped(
    config_name: Optional[str] = None,
    steps: Optional[int] = None,
    objects_retrieved: Optional[int] = None,
    total_objects: Optional[int] = None,
) -> None:
    """Notify when the simulation is manually stopped by the user."""
    lines = ["\u23f9\ufe0f Warehouse Swarm \u2014 simulazione interrotta"]

    if config_name:
        lines.append(f"Config: {config_name}")
    if steps is not None:
        lines.append(f"Steps: {steps}")
    if objects_retrieved is not None and total_objects is not None:
        lines.append(f"Oggetti: {objects_retrieved}/{total_objects}")

    await _send("\n".join(lines))


async def notify_backend_start() -> None:
    """Notify when the FastAPI backend process starts."""
    import platform
    import sys

    lines = [
        "\U0001f7e2 Warehouse Swarm \u2014 backend avviato",
        f"Host: {platform.node()}",
        f"Python: {sys.version.split()[0]}",
        f"OS: {platform.system()} {platform.release()}",
    ]
    await _send("\n".join(lines))


async def notify_backend_shutdown() -> None:
    """Notify when the FastAPI backend process is shutting down."""
    await _send("\U0001f534 Warehouse Swarm \u2014 backend spento")
