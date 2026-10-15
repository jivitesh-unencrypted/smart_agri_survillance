from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app import supabase_client as sc
from app.websocket.manager import manager

router = APIRouter()


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Authenticated live feed.

    The handshake carries the Supabase access token as `?token=` because a
    browser cannot attach headers to a WebSocket. The token is verified before
    the socket is accepted, and the socket is registered under that user's id
    so broadcasts can be scoped to them. An unauthenticated socket is closed
    with 1008 (policy violation) rather than being allowed to listen.

    Verification is a blocking network call, so it runs in the threadpool -
    doing it inline would stall the event loop for every other camera stream.
    """
    token = websocket.query_params.get("token")
    auth_user = None
    if token:
        try:
            auth_user = await run_in_threadpool(sc.verify_access_token, token)
        except Exception:  # noqa: BLE001 - any verification failure is a refusal
            auth_user = None

    if not auth_user:
        await websocket.close(code=1008, reason="Authentication required")
        return

    user_id = auth_user.get("id")
    if not user_id:
        await websocket.close(code=1008, reason="Authentication required")
        return

    await manager.connect(websocket, user_id=user_id)
    try:
        while True:
            # We don't require the client to send anything - this simply
            # keeps the connection open and lets us detect disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        await manager.disconnect(websocket, user_id=user_id)
    except Exception:
        await manager.disconnect(websocket, user_id=user_id)
