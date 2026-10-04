"""
Orchestrates CameraStreamWorker instances - at most one per camera.
The FastAPI app owns a single StreamManager instance (app.state.stream_manager).
"""
import asyncio
import time
from typing import Dict, Optional

from app.camera.stream_worker import CameraStreamWorker


class StreamManager:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._workers: Dict[int, CameraStreamWorker] = {}

    def start_camera(self, camera_id: int) -> bool:
        worker = self._workers.get(camera_id)
        if worker is not None and worker.is_running():
            return True
        worker = CameraStreamWorker(camera_id, self._loop)
        self._workers[camera_id] = worker
        worker.start()
        return True

    def stop_camera(self, camera_id: int) -> bool:
        worker = self._workers.get(camera_id)
        if worker is None:
            return False
        worker.stop()
        del self._workers[camera_id]
        return True

    def stop_all(self):
        for camera_id in list(self._workers.keys()):
            self.stop_camera(camera_id)

    def is_running(self, camera_id: int) -> bool:
        worker = self._workers.get(camera_id)
        return worker is not None and worker.is_running()

    def get_worker(self, camera_id: int) -> Optional[CameraStreamWorker]:
        return self._workers.get(camera_id)

    def active_camera_ids(self):
        return [cid for cid, w in self._workers.items() if w.is_running()]

    def mjpeg_generator(self, camera_id: int):
        """Yields multipart/x-mixed-replace JPEG frames for a browser <img> tag."""
        worker = self._workers.get(camera_id)
        boundary = b"--frame"
        while worker is not None and worker.is_running():
            jpeg = worker.get_latest_jpeg()
            if jpeg is not None:
                yield (boundary + b"\r\nContent-Type: image/jpeg\r\nContent-Length: "
                       + str(len(jpeg)).encode() + b"\r\n\r\n" + jpeg + b"\r\n")
            time.sleep(0.033)  # cap the HTTP push rate (~30fps) independent of inference rate
            worker = self._workers.get(camera_id)
