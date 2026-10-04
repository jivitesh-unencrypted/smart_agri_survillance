"""
Reusable camera capture abstraction. Supports any source cv2.VideoCapture
understands: an int device index for a local/USB webcam, or a stream
URL string (rtsp://, http://) for IP/RTSP/HTTP-MJPEG cameras. Local and
remote cameras share the exact same interface.

Adapted from the original Streamlit project's utils/camera.py.
"""
import threading
from typing import Optional, Tuple

import cv2
import numpy as np

_NETWORK_OPEN_TIMEOUT_MS = 5000


class CameraCapture:
    def __init__(self, source):
        """source: int device index, or a stream URL string."""
        self.source = source
        self._cap: Optional[cv2.VideoCapture] = None
        self._lock = threading.Lock()
        self._is_connected = False
        self._consecutive_failures = 0

    def connect(self) -> bool:
        with self._lock:
            if self._cap is not None:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None

            try:
                if isinstance(self.source, int):
                    # DirectShow backend on Windows gives more reliable webcam access.
                    self._cap = cv2.VideoCapture(self.source, cv2.CAP_DSHOW)
                    if not self._cap.isOpened():
                        self._cap = cv2.VideoCapture(self.source)
                else:
                    self._cap = cv2.VideoCapture(self.source)
                    try:
                        self._cap.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, _NETWORK_OPEN_TIMEOUT_MS)
                    except Exception:
                        pass
                    try:
                        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                    except Exception:
                        pass

                if self._cap is not None and self._cap.isOpened():
                    self._is_connected = True
                    self._consecutive_failures = 0
                    return True
                self._cap = None
                self._is_connected = False
                return False
            except Exception:
                self._cap = None
                self._is_connected = False
                return False

    def reconnect(self) -> bool:
        return self.connect()

    def read_frame(self) -> Tuple[bool, Optional[np.ndarray]]:
        with self._lock:
            if self._cap is None or not self._cap.isOpened():
                self._is_connected = False
                return False, None
            try:
                ret, frame = self._cap.read()
                if not ret or frame is None:
                    self._consecutive_failures += 1
                    return False, None
                self._consecutive_failures = 0
                self._is_connected = True
                return True, frame
            except Exception:
                self._consecutive_failures += 1
                return False, None

    def release(self):
        with self._lock:
            self._is_connected = False
            if self._cap is not None:
                try:
                    self._cap.release()
                except Exception:
                    pass
                self._cap = None

    def is_open(self) -> bool:
        with self._lock:
            return self._cap is not None and self._cap.isOpened()
