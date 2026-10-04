"""
Simple broadcast WebSocket manager. Every connected client receives
every event (camera_status, detection, alert, fps, detection_count,
system_status); the frontend filters by camera_id client-side. This
keeps the server simple while still giving true push-based real-time
updates instead of polling.
"""
import asyncio
import json
from typing import Any, Dict, Set

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self._connections: Set[WebSocket] = set()
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        async with self._lock:
            self._connections.add(websocket)

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            self._connections.discard(websocket)

    async def broadcast(self, message: Dict[str, Any]):
        payload = json.dumps(message, default=str)
        dead = []
        async with self._lock:
            connections = list(self._connections)
        for ws in connections:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        if dead:
            async with self._lock:
                for ws in dead:
                    self._connections.discard(ws)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


manager = ConnectionManager()


def broadcast_sync(loop: asyncio.AbstractEventLoop, message: Dict[str, Any]):
    """
    Schedules a broadcast from a non-async context (the camera worker
    threads run outside the event loop). Fire-and-forget by design -
    a dropped telemetry message should never block detection.
    """
    try:
        asyncio.run_coroutine_threadsafe(manager.broadcast(message), loop)
    except Exception:
        pass
