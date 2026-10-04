"""
YOLO detector wrapper. The model is loaded once (see app/main.py
startup) and shared across every camera worker - it is never reloaded
per-request or per-camera, per the performance requirements.

Local inference only: ultralytics runs the .pt weights on-device
(CPU or local GPU if available) - no frames are ever sent anywhere
external.
"""
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import cv2
import numpy as np

from app.core.config import settings, CATEGORIES, CATEGORY_BOX_COLOR_BGR


class YOLODetector:
    def __init__(self, model_path: str = None):
        from ultralytics import YOLO  # imported lazily so the API can still start (with a clear
                                       # error) even if ultralytics/torch aren't installed yet.

        path = model_path or str(settings.resolved_model_path)
        self.model = YOLO(path)
        self.model_path = path
        # Ultralytics sets `model_name` to the full path it was given, which
        # is noise in a status payload - report just the weights filename.
        # The full path is still available via `model_path` / MODEL_PATH.
        self.model_name = Path(path).name

    def detect_frame(
        self,
        frame_bgr: np.ndarray,
        conf_threshold: float = None,
        iou_threshold: float = None,
        draw: bool = True,
    ) -> Tuple[np.ndarray, List[Dict[str, Any]], Dict[str, int], float]:
        """
        Runs detection on a single BGR frame.
        Returns (annotated_frame_bgr, detections, counts, inference_ms).
        """
        conf = conf_threshold if conf_threshold is not None else settings.DEFAULT_CONFIDENCE_THRESHOLD
        iou = iou_threshold if iou_threshold is not None else settings.DEFAULT_IOU_THRESHOLD

        start = time.monotonic()
        results = self.model(frame_bgr, conf=conf, iou=iou, verbose=False)[0]
        inference_ms = (time.monotonic() - start) * 1000.0

        detections: List[Dict[str, Any]] = []
        counts = {"Human": 0, "Animals": 0, "Vehicles": 0, "Others": 0}
        annotated = frame_bgr.copy() if draw else frame_bgr

        for box in results.boxes:
            cls_id = int(box.cls[0].item())
            conf_score = float(box.conf[0].item())
            xyxy = box.xyxy[0].tolist()
            name = self.model.names[cls_id]

            category = "Others"
            for cat_name, items in CATEGORIES.items():
                if name in items:
                    category = cat_name
                    break

            counts[category] += 1
            detections.append({
                "class_id": cls_id,
                "name": name,
                "category": category,
                "confidence": conf_score,
                "bbox": xyxy,
            })

            if draw:
                x1, y1, x2, y2 = map(int, xyxy)
                color = CATEGORY_BOX_COLOR_BGR.get(category, (128, 128, 128))
                cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
                label = f"{name} {conf_score:.0%} ({category})"
                cv2.putText(annotated, label, (x1, max(y1 - 10, 10)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

        return annotated, detections, counts, inference_ms
