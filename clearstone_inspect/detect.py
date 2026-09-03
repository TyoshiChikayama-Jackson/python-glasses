"""
Batch object detection over a job's extracted frames. Replaces the
per-frame streaming detection in analyze.py — there's no live-feed
latency constraint anymore, so this loads a single, larger model once
and runs it over every frame path in one batch, with marker frames
additionally getting a second higher-resolution pass.

Model weights (*.pt) are downloaded automatically by ultralytics on
first run and are not checked into the repo — see .gitignore.
"""

import os

import cv2
from ultralytics import YOLO

from utils import ensure_dir

JOBS_DIR = "jobs"

# Module-level constants so the preferred/fallback models are easy to
# change in one place later.
PREFERRED_MODEL = "yolov8m-oiv7.pt"
FALLBACK_MODEL = "yolov8n-oiv7.pt"

BASELINE_IMGSZ = 640
MARKER_IMGSZ = 1280  # higher-resolution second pass for marker frames

_model = None
_model_name = None


def get_model():
    """
    Loads the detection model once (module-level cache). Tries
    PREFERRED_MODEL first — since batch processing has no latency
    constraint, prefer accuracy over speed — and falls back to
    FALLBACK_MODEL if the preferred model isn't available (not on disk
    and, if ultralytics tries to auto-download it, that fails too).
    """
    global _model, _model_name

    if _model is not None:
        return _model

    try:
        print(f"[detect] Loading model {PREFERRED_MODEL}...")
        _model = YOLO(PREFERRED_MODEL)
        _model_name = PREFERRED_MODEL
    except Exception as exc:
        print(f"[detect] Could not load {PREFERRED_MODEL} ({exc}); "
              f"falling back to {FALLBACK_MODEL}.")
        _model = YOLO(FALLBACK_MODEL)
        _model_name = FALLBACK_MODEL

    print(f"[detect] Using model: {_model_name}")
    return _model


def _run_model(model, image, imgsz):
    results = model(image, imgsz=imgsz, verbose=False)[0]
    detections = []
    for box in results.boxes:
        cls_id = int(box.cls[0])
        cls_name = model.names[cls_id]
        conf = float(box.conf[0])

        x1, y1, x2, y2 = box.xyxy[0].tolist()
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)

        detections.append({
            "class": cls_name,
            "confidence": round(conf * 100, 1),
            "bbox": {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1},
        })
    return detections


def _draw_detections(image, detections):
    annotated = image.copy()
    for det in detections:
        bbox = det["bbox"]
        rx, ry, rw, rh = bbox["x"], bbox["y"], bbox["w"], bbox["h"]
        conf = det["confidence"]
        cls = det["class"]

        if conf >= 75:
            box_color = (0, 0, 255)
        elif conf >= 50:
            box_color = (0, 255, 255)
        else:
            box_color = (0, 255, 0)

        cv2.rectangle(annotated, (rx, ry), (rx + rw, ry + rh), box_color, 2)
        label = f"{cls}: {conf}%"
        cv2.putText(annotated, label, (rx, max(ry - 8, 0)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, box_color, 2)
    return annotated


def detect_frames(job_id, frames):
    """
    Runs detection over every frame in `frames` (the list returned by
    extract.extract_frames(): [{path, timestamp, source}, ...]).

    Marker frames (source == "marker") get a second, higher-resolution
    pass (MARKER_IMGSZ) in addition to the baseline pass, and detections
    from both passes are merged (duplicates aren't explicitly
    deduplicated here — the higher-res pass typically finds a superset
    of what the lower-res pass finds, and correlate.py only cares about
    presence/confidence per class, not exact box counts).

    Writes annotated copies of any frame that has at least one detection
    to jobs/<job_id>/annotated/.

    Returns a list of {path, timestamp, source, detections} — one entry
    per input frame, in the same order.
    """
    job_dir = os.path.join(JOBS_DIR, job_id)
    annotated_dir = ensure_dir(os.path.join(job_dir, "annotated"))

    model = get_model()

    results = []
    for i, frame_info in enumerate(frames, 1):
        path = frame_info["path"]
        source = frame_info.get("source", "baseline")

        image = cv2.imread(path)
        if image is None:
            print(f"[detect] Could not read frame: {path} — skipping.")
            results.append({**frame_info, "detections": []})
            continue

        detections = _run_model(model, image, BASELINE_IMGSZ)

        if source == "marker":
            higher_res_detections = _run_model(model, image, MARKER_IMGSZ)
            detections = detections + higher_res_detections

        print(f"[detect] ({i}/{len(frames)}) {os.path.basename(path)} "
              f"[{source}]: {len(detections)} detection(s)")

        if detections:
            annotated = _draw_detections(image, detections)
            annotated_path = os.path.join(annotated_dir, os.path.basename(path))
            cv2.imwrite(annotated_path, annotated)

        results.append({**frame_info, "detections": detections})

    return results


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python detect.py <job_id>")
        sys.exit(1)

    job_id = sys.argv[1]
    frames_dir = os.path.join(JOBS_DIR, job_id, "frames")
    if not os.path.isdir(frames_dir):
        print(f"No frames found at {frames_dir} — run extract.py first.")
        sys.exit(1)

    fake_frames = []
    for filename in sorted(os.listdir(frames_dir)):
        if filename.endswith(".jpg"):
            timestamp = float(filename.replace("frame_", "").replace(".jpg", ""))
            fake_frames.append({
                "path": os.path.join(frames_dir, filename),
                "timestamp": timestamp,
                "source": "baseline",
            })

    detections = detect_frames(job_id, fake_frames)
    print(json.dumps(detections, indent=2))
