"""
Camera routes - all of these require a Supabase-authenticated user
because camera rows carry the stream configuration and the credentials
that actually open the streams.
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app import supabase_client as sc
from app.api.deps import require_authenticated_user
from app.camera.testing import test_connection
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
    cameras = camera_service.list_cameras()
    return [camera_service.camera_to_out_dict(c) for c in cameras]


@router.post("", response_model=CameraOut, status_code=201)
def create_camera(payload: CameraCreate, user: dict = Depends(require_authenticated_user)):
    if payload.connection_type != "local_webcam" and not (payload.stream_url or "").strip():
        raise HTTPException(status_code=422, detail="stream_url is required for IP / RTSP / HTTP cameras")
    camera = camera_service.create_camera(payload.model_dump())
    return camera_service.camera_to_out_dict(camera)


@router.get("/{camera_id}", response_model=CameraOut)
def get_camera(camera_id: int, user: dict = Depends(require_authenticated_user)):
    camera = camera_service.get_camera(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera_service.camera_to_out_dict(camera)


@router.put("/{camera_id}", response_model=CameraOut)
def update_camera(camera_id: int, payload: CameraUpdate, user: dict = Depends(require_authenticated_user)):
    if payload.connection_type != "local_webcam" and not (payload.stream_url or "").strip():
        raise HTTPException(status_code=422, detail="stream_url is required for IP / RTSP / HTTP cameras")
    camera = camera_service.update_camera(camera_id, payload.model_dump())
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    return camera_service.camera_to_out_dict(camera)


@router.delete("/{camera_id}", status_code=204)
def delete_camera(camera_id: int, request: Request, user: dict = Depends(require_authenticated_user)):
    _stream_manager(request).stop_camera(camera_id)
    if not camera_service.delete_camera(camera_id):
        raise HTTPException(status_code=404, detail="Camera not found")


@router.post("/{camera_id}/test", response_model=CameraTestResult)
def test_camera(camera_id: int, user: dict = Depends(require_authenticated_user)):
    camera = camera_service.get_camera(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")

    result = test_connection(camera_service.build_source(camera))

    # A test still counts as a health observation even offline.
    status_str = "online" if result["success"] else "offline"
    try:
        updates = {"status": status_str}
        if result["success"]:
            updates["last_latency_ms"] = result["latency_ms"]
        updates["last_error"] = None if result["success"] else result["message"]
        cameras_repo.update_camera(camera_id, updates)
    except sc.SupabaseUnavailable:
        pass

    return result


@router.post("/{camera_id}/start")
def start_camera(camera_id: int, request: Request, user: dict = Depends(require_authenticated_user)):
    camera = camera_service.get_camera(camera_id)
    if camera is None:
        raise HTTPException(status_code=404, detail="Camera not found")
    if not camera.get("enabled"):
        raise HTTPException(status_code=400, detail="Camera is disabled")
    _stream_manager(request).start_camera(camera_id)
    return {"status": "starting"}


@router.post("/{camera_id}/stop")
def stop_camera(camera_id: int, request: Request, user: dict = Depends(require_authenticated_user)):
    _stream_manager(request).stop_camera(camera_id)
    return {"status": "stopped"}


@router.get("/{camera_id}/stream")
async def stream_camera(camera_id: int, request: Request, user: dict = Depends(require_authenticated_user)):
    """
    MJPEG stream of the annotated frames.

    Authenticated through the standard bearer token. Because a browser
    cannot attach headers to an `<img src>`, the frontend also passes the
    token as `?token=`; `require_authenticated_user` accepts either.
    """
    stream_manager = _stream_manager(request)
    if not stream_manager.is_running(camera_id):
        raise HTTPException(status_code=409, detail="Camera is not currently running - start it first")
    return StreamingResponse(
        stream_manager.mjpeg_generator(camera_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"},
    )
