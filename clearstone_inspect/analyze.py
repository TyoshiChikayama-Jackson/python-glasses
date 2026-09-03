import cv2
import json
import numpy as np
import os
from datetime import datetime
from ultralytics import YOLO
from logger import log_result


_model_oiv7 = None
_model_yolo26 = None


def get_models():
    global _model_oiv7, _model_yolo26
    if _model_oiv7 is None:
        _model_oiv7 = YOLO("yolov8n-oiv7.pt")
    if _model_yolo26 is None:
        _model_yolo26 = YOLO("yolo26n.pt")
    return _model_oiv7, _model_yolo26


def print_model_info():
    m1, m2 = get_models()
    print(f"Model 1: {m1.model_name} ({len(m1.names)} classes)")
    print(f"Model 2: {m2.model_name} ({len(m2.names)} classes)")
    print()


def compute_iou(a, b):
    ax1, ay1, ax2, ay2 = a["x"], a["y"], a["x"] + a["w"], a["y"] + a["h"]
    bx1, by1, bx2, by2 = b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]

    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)

    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    area_a = a["w"] * a["h"]
    area_b = b["w"] * b["h"]
    union = area_a + area_b - inter

    if union == 0:
        return 0.0
    return inter / union


def remove_duplicates(detections, iou_threshold=0.5):
    detections.sort(key=lambda d: d["confidence"], reverse=True)
    kept = []
    for det in detections:
        is_dup = False
        for k in kept:
            if compute_iou(det, k) > iou_threshold:
                is_dup = True
                break
        if not is_dup:
            kept.append(det)
    return kept


def load_violations(file_path="violations.json"):
    with open(file_path, "r") as file:
        data = json.load(file)
    return data["violations"]


def _run_single_model(model, frame):
    results = model(frame, verbose=False)[0]
    detections = []
    for box in results.boxes:
        cls_id = int(box.cls[0])
        cls_name = model.names[cls_id]
        conf = float(box.conf[0])

        x1, y1, x2, y2 = box.xyxy[0].tolist()
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)

        detections.append({
            "x": x1,
            "y": y1,
            "w": x2 - x1,
            "h": y2 - y1,
            "class": cls_name,
            "confidence": round(conf * 100, 1),
        })
    return detections


def run_yolo(frame):
    m1, m2 = get_models()
    dets1 = _run_single_model(m1, frame)
    dets2 = _run_single_model(m2, frame)
    merged = dets1 + dets2
    return remove_duplicates(merged)


def classify_result(confidence, threshold_percent):
    if confidence >= threshold_percent:
        return "VIOLATION DETECTED"
    elif confidence >= threshold_percent * 0.67:
        return "NEEDS HUMAN REVIEW"
    else:
        return "PASS"


def draw_detections(frame, detections):
    for detection in detections:
        for region in detection.get("region_details", []):
            rx, ry, rw, rh = region["x"], region["y"], region["w"], region["h"]
            rc = region["confidence"]
            cls = region["class"]

            if rc >= 75:
                box_color = (0, 0, 255)
            elif rc >= 50:
                box_color = (0, 255, 255)
            else:
                box_color = (0, 255, 0)

            cv2.rectangle(frame, (rx, ry), (rx + rw, ry + rh), box_color, 2)
            box_label = f"{cls}: {rc}%"
            cv2.putText(frame, box_label, (rx, ry - 8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, box_color, 2)

    return frame


def analyze_image(image_path, voice_note=None, video_path=None):
    if not os.path.exists(image_path):
        print(f"Error: File not found — {image_path}")
        return None

    image = cv2.imread(image_path)
    if image is None:
        print(f"Error: Could not read image — {image_path}")
        return None

    violations = load_violations()
    regions = run_yolo(image)

    results = []

    for violation in violations:
        threshold_percent = violation["confidence_threshold"] * 100

        if regions:
            max_conf = max(r["confidence"] for r in regions)
        else:
            max_conf = 0.0

        status = classify_result(max_conf, threshold_percent)

        result = {
            "violation_id": violation["id"],
            "violation_name": violation["name"],
            "description": violation["description"],
            "confidence": max_conf,
            "status": status,
            "urgency": violation["urgency"],
            "trade_responsible": violation["trade_responsible"],
            "code_reference": violation["code_reference"],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "image_path": image_path,
            "voice_note": voice_note,
            "video_path": video_path,
            "details": {
                "detections": len(regions),
                "classes": list(set(r["class"] for r in regions)),
            },
        }
        results.append(result)
        log_result(result)

        print(f"\n{'=' * 50}")
        print(f"  ANALYSIS RESULT")
        print(f"{'=' * 50}")
        print(f"  Violation:    {result['violation_name']}")
        print(f"  Status:       {result['status']}")
        print(f"  Confidence:   {result['confidence']}%")
        print(f"  Urgency:      {result['urgency']}")
        print(f"  Trade:        {result['trade_responsible']}")
        print(f"  Code Ref:     {result['code_reference']}")
        print(f"  Timestamp:    {result['timestamp']}")
        print(f"  Detections:   {result['details']['detections']}")
        print(f"  Classes:      {', '.join(result['details']['classes'])}")
        if voice_note:
            print(f"  Voice Note:   {voice_note}")
        print(f"{'=' * 50}")
        if regions:
            print(f"  Bounding boxes:")
            for r in regions:
                print(f"    {r['class']}: {r['confidence']}% at ({r['x']},{r['y']},{r['w']},{r['h']})")
        print(f"{'=' * 50}\n")

    return results


def analyze_video(video_path):
    if not os.path.exists(video_path):
        print(f"Error: File not found — {video_path}")
        return None

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Could not open video — {video_path}")
        return None

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    sample_interval = max(frame_count // 5, 1)

    best_result = None
    best_confidence = 0
    frame_index = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_index % sample_interval == 0:
            temp_path = os.path.join("output", "_temp_frame.jpg")
            cv2.imwrite(temp_path, frame)
            results = analyze_image(temp_path)

            if results and results[0]["confidence"] > best_confidence:
                best_confidence = results[0]["confidence"]
                best_result = results

            if os.path.exists(temp_path):
                os.remove(temp_path)

        frame_index += 1

    cap.release()

    if best_result:
        best_result[0]["image_path"] = video_path
        print(f"\nVideo analysis complete. Highest confidence: {best_confidence}%")

    return best_result
