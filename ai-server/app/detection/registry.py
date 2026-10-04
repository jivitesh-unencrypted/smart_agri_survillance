"""
Process-wide detector singleton. Loaded once in app/main.py's startup
event and reused by every camera worker thread, per the "load the
model once" performance requirement.
"""
from typing import Optional

from app.detection.detector import YOLODetector

_detector: Optional[YOLODetector] = None
_load_error: Optional[str] = None


def load_detector() -> None:
    global _detector, _load_error
    try:
        _detector = YOLODetector()
        _load_error = None
    except Exception as e:  # missing weights, missing ultralytics/torch, etc.
        _detector = None
        _load_error = str(e)


def get_detector() -> Optional[YOLODetector]:
    return _detector


def get_load_error() -> Optional[str]:
    return _load_error
