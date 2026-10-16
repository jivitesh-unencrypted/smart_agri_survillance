"""
Camera routes - all of these require a Supabase-authenticated user
because camera rows carry the stream configuration and the credentials
that actually open the streams.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app import supabase_client as sc
from app.api.deps import require_authenticated_user
from app.api.live_detection import discard_browser_session
from app.camera.testing import test_connection
from app.core.config import BROWSER_CONNECTION_TYPES, NETWORK_CONNECTION_TYPES
from app.repositories import cameras as cameras_repo
from app.schemas.camera import CameraCreate, CameraOut, CameraTestResult, CameraUpdate
from app.services import camera_service

router = APIRouter(prefix="/api/cameras", tags=["cameras"])


def _stream_manager(request: Request):
    manager = getattr(request.app.state, "stream_manager", None)
    if manager is None:
        raise HTTPException(status_code=503, detail="Stream manager is not ready")
    return manager


@router.get("", response_model=list[CameraOut])
def list_cameras(user: dict = Depends(require_authenticated_user)):
    cameras = camera_service.list_cameras(user_id=user["id"])
    return [camera_service.camera_to_out_dict(c) for c in cameras]


@router.post("", response_model=CameraOut, status_code=201)
def create_camera(payload: CameraCreate, user: dict = Depends(require_authenticated_user)):
    if payload.connection_type in NETWORK_CONNECTION_TYPES and not (payload.stream_url or "").strip():
        raise HTTPException(status_code=422, detail="stream_url is required for IP / RTSP / HTTP cameras")
    # The owner comes from the verified access token, not from the request body.
    camera = camera_service.create_camera(payload.model_dump(), user_id=user["id"])
    return camera_service.camera_to_out_dict(camera)


@router.get("/{camera_id}", response_model=CameraOut)
def get_camera(camera_id: int, user: dict = Depends(require_authenticated_user)):
    # Scoped by owner: another user's camera is reported as 404, not 403, so
    # the API does not confirm that an id exists.
    camera = camera_service.get_camera(camera_id, user_id=user["id"])
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera_service.camera_to_out_dict(camera)


@router.put("/{camera_id}", response_model=CameraOut)
def update_camera(camera_id: int, payload: CameraUpdate, user: dict = Depends(require_authenticated_user)):
    if payload.connection_type in NETWORK_CONNECTION_TYPES and not (payload.stream_url or "").strip():
        raise HTTPException(status_code=422, detail="stream_url is required for IP / RTSP / HTTP cameras")
    camera = camera_service.update_camera(camera_id, payload.model_dump(), user_id=user["id"])
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera_service.camera_to_out_dict(camera)


@router.delete("/{camera_id}", status_code=204)
def delete_camera(camera_id: int, request: Request, user: dict = Depends(require_authenticated_user)):
    # Ownership is checked before the stop, so a caller cannot stop somebody
    # else's running camera by guessing its id.
    camera = camera_service.get_camera(camera_id, user_id=user["id"])
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")

    _stream_manager(request).stop_camera(camera_id)
    discard_browser_session(user["id"], camera_id)
    if not camera_service.delete_camera(camera_id, user_id=user["id"]):
        raise HTTPException(status_code=404, detail="Camera not found")


_BROWSER_CAMERA_MESSAGE = "Browser webcams run in the browser. Open Live Monitoring to start one."


@router.post("/{camera_id}/test", response_model=CameraTestResult)
def test_camera(camera_id: int, user: dict = Depends(require_authenticated_user)):
    camera = camera_service.get_camera(camera_id, user_id=user["id"])
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    if camera.get("connection_type") in BROWSER_CONNECTION_TYPES:
        raise HTTPException(status_code=400, detail=_BROWSER_CAMERA_MESSAGE)

    result = test_connection(camera_service.build_source(camera))

    # A test still counts as a health observation even offline.
    status_str = "online" if result["success"] else "offline"
    try:
        updates = {"status": status_str}
        if result["success"]:
            updates["last_latency_ms"] = result["latency_ms"]
        updates["last_error"] = None if result["success"] else result["message"]
        cameras_repo.update_camera(camera_id, updates, user_id=user["id"])
    except sc.SupabaseUnavailable:
        pass

    return result


@router.post("/{camera_id}/start")
def start_camera(camera_id: int, request: Request, user: dict = Depends(require_authenticated_user)):
    camera = camera_service.get_camera(camera_id, user_id=user["id"])
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    if camera.get("connection_type") in BROWSER_CONNECTION_TYPES:
        raise HTTPException(status_code=400, detail=_BROWSER_CAMERA_MESSAGE)
    if not camera.get("enabled"):
        raise HTTPException(status_code=400, detail="Camera is disabled")
    _stream_manager(request).start_camera(
    camera_id,
    user_id=user["id"],
    )
    return {"status": "starting"}


@router.post("/{camera_id}/stop")
def stop_camera(camera_id: int, request: Request, user: dict = Depends(require_authenticated_user)):
    camera = camera_service.get_camera(camera_id, user_id=user["id"])
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    _stream_manager(request).stop_camera(camera_id)
    return {"status": "stopped"}


@router.get("/{camera_id}/stream")
async def stream_camera(camera_id: int, request: Request, user: dict = Depends(require_authenticated_user)):
    """
    MJPEG stream of the annotated frames.

    Authenticated through the standard bearer token. Because a browser
    cannot attach headers to an `<img src>`, the frontend also passes the
    token as `?token=`; `require_authenticated_user` accepts either.

    Ownership is enforced here too - otherwise a valid token would let any
    signed-in user pull any camera's video feed.
    """
    camera = camera_service.get_camera(camera_id, user_id=user["id"])
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")

    stream_manager = _stream_manager(request)
    if not stream_manager.is_running(camera_id):
        raise HTTPException(status_code=409, detail="Camera is not currently running - start it first")
    return StreamingResponse(
        stream_manager.mjpeg_generator(camera_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )
