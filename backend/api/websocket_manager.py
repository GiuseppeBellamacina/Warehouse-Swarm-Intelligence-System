"""
WebSocket manager for real-time simulation streaming
"""

from typing import Dict, Set

import socketio


class WebSocketManager:
    """
    Manages WebSocket connections and broadcasting simulation state.
    Each client is associated with a session_id so that broadcasts
    remain isolated between different users.
    """

    def __init__(self):
        # Create Socket.IO server
        self.sio = socketio.AsyncServer(
            async_mode="asgi", cors_allowed_origins="*", logger=False, engineio_logger=False
        )

        # Track connected clients
        self.clients: Set[str] = set()

        # Map socket-id → session-id
        self.client_sessions: Dict[str, str] = {}

        # Setup event handlers
        self._setup_handlers()

    def _setup_handlers(self) -> None:
        """Setup Socket.IO event handlers"""

        @self.sio.event
        async def connect(sid, environ, auth):
            """Handle client connection"""
            self.clients.add(sid)
            session_id = (auth or {}).get("sessionId", "default")
            self.client_sessions[sid] = session_id
            print(f"Client connected: {sid} session={session_id} (Total: {len(self.clients)})")
            await self.sio.emit("connection_established", {"sid": sid}, room=sid)

        @self.sio.event
        async def disconnect(sid):
            """Handle client disconnection"""
            session_id = self.client_sessions.pop(sid, None)
            self.clients.discard(sid)
            print(
                f"Client disconnected: {sid} session={session_id} (Remaining: {len(self.clients)})"
            )

            # If no clients remain for the session, stop its simulation to free resources
            if session_id and session_id != "default":
                remaining = [s for s, sess in self.client_sessions.items() if sess == session_id]
                if not remaining:
                    from backend.api.session_registry import session_registry

                    mgr = session_registry.get(session_id)
                    if mgr and mgr.is_running:
                        mgr.stop_simulation()
                        print(f"[session] stopped simulation for abandoned session {session_id}")

    # ── generic (legacy) broadcasts ──────────────────────────────────────────

    async def broadcast_state(self, state: Dict) -> None:
        """Broadcast simulation state to all connected clients"""
        if self.clients:
            await self.sio.emit("simulation_state", state)

    async def broadcast_event(self, event_name: str, data: Dict) -> None:
        """Broadcast a custom event to all clients"""
        if self.clients:
            await self.sio.emit(event_name, data)

    # ── session-scoped broadcasts ─────────────────────────────────────────────

    async def broadcast_to_session(self, session_id: str, event_name: str, data: Dict) -> None:
        """Send an event only to clients belonging to *session_id*."""
        for sid, sess in list(self.client_sessions.items()):
            if sess == session_id:
                await self.sio.emit(event_name, data, room=sid)

    async def broadcast_state_to_session(self, session_id: str, state: Dict) -> None:
        await self.broadcast_to_session(session_id, "simulation_state", state)

    async def broadcast_event_to_session(
        self, session_id: str, event_name: str, data: Dict
    ) -> None:
        await self.broadcast_to_session(session_id, event_name, data)

    # ── direct client messaging ───────────────────────────────────────────────

    async def send_to_client(self, sid: str, event_name: str, data: Dict) -> None:
        """Send data to a specific client"""
        await self.sio.emit(event_name, data, room=sid)

    def get_asgi_app(self):
        """Get ASGI app for Socket.IO"""
        return socketio.ASGIApp(self.sio)


# Global WebSocket manager instance
ws_manager = WebSocketManager()
