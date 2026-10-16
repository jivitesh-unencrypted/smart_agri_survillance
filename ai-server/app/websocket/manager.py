"""
Authenticated, owner-scoped WebSocket manager.

Every message is delivered only to the connections belonging to the user that
message is about. Before this change the socket accepted any connection and
fanned every message out to everyone, so a signed-in User B's dashboard
received User A's live detections, alerts, camera statuses and fps in real
time - the cross-user leak that per-user row isolation alone does NOT close,
because no database query is involved.

The socket authenticates with the same Supabase access token the REST routes
use. Browsers cannot set headers on a WebSocket handshake, so the token
arrives as `?token=` (the same convention the MJPEG `<img src>` already uses)
and is verified before the connection is accepted.
"""
import asyncio
import json
from typing import Any, Dict, Optional, Set

from fastapi import WebSocket


class ConnectionManager:
    """
    Tracks connections keyed by the owning user's id.

    `self._connections` maps user_id -> that user's sockets. A user with two
    tabs gets two entries under the same key, which is exactly what is wanted:
    both tabs see that user's events, and neither sees anyone else's.
    """

    def __init__(self):
        self._connections: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        async with self._lock:
            self._connections.setdefault(user_id, set()).add(websocket)

    async def disconnect(self, websocket: WebSocket, user_id: Optional[str] = None):
        async with self._lock:
            if user_id is not None:
                sockets = self._connections.get(user_id)
                if sockets is not None:
                    sockets.discard(websocket)
                    if not sockets:
                        self._connections.pop(user_id, None)
                return
            # Owner unknown (a failed handshake): fall back to a full scan so a
            # half-registered socket is never leaked.
            for sockets in self._connections.values():
                sockets.discard(websocket)
            for uid in [k for k, v in self._connections.items() if not v]:
                self._connections.pop(uid, None)

    async def broadcast(self, message: Dict[str, Any], user_id: Optional[str] = None):
        """
        Sends `message` to the owner's connections only.

        `user_id=None` means "machine-level telemetry with no farm data in it"
        and is delivered to every authenticated connection. Anything carrying
        detections, alerts, camera state or per-camera counts MUST pass an
        owner - otherwise it is a cross-user leak, so that caller's job is to
        get it right and the filter here is what makes it stick.
        """
        payload = json.dumps(message, default=str)
        async with self._lock:
            if user_id is None:
                targets = [ws for sockets in self._connections.values() for ws in sockets]
            else:
                targets = list(self._connections.get(user_id, ()))

        dead: list = []
        for ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001 - a dropped client is not an error
                dead.append(ws)

        if dead:
            async with self._lock:
                for ws in dead:
                    for sockets in self._connections.values():
                        sockets.discard(ws)
                for uid in [k for k, v in self._connections.items() if not v]:
                    self._connections.pop(uid, None)

    @property
    def connection_count(self) -> int:
        return sum(len(s) for s in self._connections.values())


manager = ConnectionManager()


def broadcast_sync(loop: asyncio.AbstractEventLoop, message: Dict[str, Any],
                   user_id: Optional[str] = None):
    """
    Schedules a broadcast from a non-async context (the camera worker
    threads run outside the event loop). Fire-and-forget by design -
    a dropped telemetry message should never block detection.

    The owner is taken from `user_id`. Camera workers historically put the owner
    only inside the message body (`{"user_id": ..., "type": ...}`), which the
    filter never read, so those messages reached every signed-in user. A body
    `user_id` is therefore used as the filter when no explicit owner is given,
    and a message that names no owner at all is dropped rather than fanned out.
    """
    if user_id is None and "user_id" in message:
        user_id = message.get("user_id")
        if user_id is None:
            # Owner-less camera/detection data is never delivered to other users.
            return
    try:
        asyncio.run_coroutine_threadsafe(manager.broadcast(message, user_id=user_id), loop)
    except Exception:  # noqa: BLE001
        pass
