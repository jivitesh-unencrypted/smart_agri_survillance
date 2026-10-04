"""Camera connection testing - opens the stream, reads one frame, releases it."""
import time

from app.camera.capture import CameraCapture


def test_connection(source) -> dict:
    """
    Never raises; never leaks raw exception text to the caller.
    Returns {"success": bool, "message": str, "latency_ms": float | None}.
    """
    cap = CameraCapture(source=source)
    start = time.monotonic()
    try:
        if not cap.connect():
            return {
                "success": False,
                "message": "Unable to open camera stream. Check the stream URL, network connectivity, "
                            "camera availability, and username/password.",
                "latency_ms": None,
            }
        ret, frame = cap.read_frame()
        latency_ms = (time.monotonic() - start) * 1000.0
        if not ret or frame is None:
            return {
                "success": False,
                "message": "Connected, but no video frame was received. Check the camera's stream format "
                            "and network stability.",
                "latency_ms": None,
            }
        return {"success": True, "message": "Stream is available.", "latency_ms": latency_ms}
    except Exception:
        return {
            "success": False,
            "message": "Unable to open camera stream. Check the stream URL, network connectivity, "
                        "camera availability, and username/password.",
            "latency_ms": None,
        }
    finally:
        cap.release()
