"""
WebSocket manager for real-time simulation streaming
"""

from typing import Dict, Set

import socketio


class WebSocketManager:
    """
    Manages WebSocket connections and broadcasting simulation state
    """

    def __init__(self):
        # Create Socket.IO server
        self.sio = socketio.AsyncServer(
            async_mode="asgi", cors_allowed_origins="*", logger=False, engineio_logger=False
        )

        # Track connected clients
        self.clients: Set[str] = set()

        # Setup event handlers
        self._setup_handlers()

    def _setup_handlers(self) -> None:
        """Setup Socket.IO event handlers"""

        @self.sio.event
        async def connect(sid, environ):
            """Handle client connection"""
            self.clients.add(sid)
            print(f"Client connected: {sid} (Total: {len(self.clients)})")
            await self.sio.emit("connection_established", {"sid": sid}, room=sid)

        @self.sio.event
        async def disconnect(sid):
            """Handle client disconnection"""
            self.clients.discard(sid)
            print(f"Client disconnected: {sid} (Remaining: {len(self.clients)})")

    async def broadcast_state(self, state: Dict) -> None:
        """
        Broadcast simulation state to all connected clients

        Args:
            state: Simulation state dictionary
        """
        if self.clients:
            await self.sio.emit("simulation_state", state)

    async def broadcast_event(self, event_name: str, data: Dict) -> None:
        """
        Broadcast a custom event to all clients

        Args:
            event_name: Name of the event
            data: Event data
        """
        if self.clients:
            await self.sio.emit(event_name, data)

    async def send_to_client(self, sid: str, event_name: str, data: Dict) -> None:
        """
        Send data to a specific client

        Args:
            sid: Socket ID of client
            event_name: Event name
            data: Data to send
        """
        await self.sio.emit(event_name, data, room=sid)

    def get_asgi_app(self):
        """Get ASGI app for Socket.IO"""
        return socketio.ASGIApp(self.sio)


# Global WebSocket manager instance
ws_manager = WebSocketManager()
