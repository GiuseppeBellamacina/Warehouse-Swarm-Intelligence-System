"""
Per-session SimulationManager registry.
Each browser tab/session gets an isolated SimulationManager instance.
Idle sessions are automatically cleaned up after IDLE_TIMEOUT seconds.
"""

import asyncio
import time
from typing import Dict, Optional

from backend.api.simulation_manager import SimulationManager

IDLE_TIMEOUT = 300  # 5 minutes


class SessionRegistry:
    def __init__(self) -> None:
        self._managers: Dict[str, SimulationManager] = {}
        self._last_seen: Dict[str, float] = {}

    def get(self, session_id: str) -> Optional[SimulationManager]:
        """Return an existing SimulationManager or None."""
        self._touch(session_id)
        return self._managers.get(session_id)

    def get_or_create(self, session_id: str) -> SimulationManager:
        """Return existing or create a new SimulationManager for this session."""
        self._touch(session_id)
        if session_id not in self._managers:
            self._managers[session_id] = SimulationManager()
            print(f"[session] created session {session_id}")
        return self._managers[session_id]

    def remove(self, session_id: str) -> None:
        """Stop and remove a session."""
        mgr = self._managers.pop(session_id, None)
        self._last_seen.pop(session_id, None)
        if mgr:
            mgr.stop_simulation()
            print(f"[session] removed session {session_id}")

    def _touch(self, session_id: str) -> None:
        self._last_seen[session_id] = time.monotonic()

    async def cleanup_loop(self) -> None:
        """Background task: periodically remove sessions that have been idle too long."""
        while True:
            await asyncio.sleep(60)
            now = time.monotonic()
            stale = [
                sid
                for sid, ts in list(self._last_seen.items())
                if now - ts > IDLE_TIMEOUT
            ]
            for sid in stale:
                print(f"[session] purging idle session {sid}")
                self.remove(sid)


session_registry = SessionRegistry()
