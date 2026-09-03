import glob
import json
import os
from datetime import datetime


LOG_FILE = os.path.join("output", "inspection_log.json")
OUTPUT_DIR = "output"


def load_log():
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, "r") as file:
            return json.load(file)
    return {"inspections": []}


def save_log(log_data):
    with open(LOG_FILE, "w") as file:
        json.dump(log_data, file, indent=2)


def archive_and_clear_log():
    """
    Renames the current inspection_log.json with a timestamp (archiving
    it) and starts a fresh, empty log in its place. Returns the archive
    path, or None if there was no existing log to archive.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    archive_path = None
    if os.path.exists(LOG_FILE):
        stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        archive_path = os.path.join(OUTPUT_DIR, f"inspection_log_{stamp}.json")
        os.replace(LOG_FILE, archive_path)

    save_log({"inspections": []})
    return archive_path


def delete_all_inspections():
    """
    Permanently deletes every file in the output folder (logs, photos,
    videos, reports, archives — everything). Returns the number of files
    removed.
    """
    if not os.path.isdir(OUTPUT_DIR):
        return 0

    removed = 0
    for path in glob.glob(os.path.join(OUTPUT_DIR, "*")):
        if os.path.isfile(path):
            os.remove(path)
            removed += 1

    return removed


def load_violations_lookup():
    vio_path = "violations.json"
    if not os.path.exists(vio_path):
        return {}
    with open(vio_path, "r") as file:
        data = json.load(file)
    lookup = {}
    for v in data.get("violations", []):
        for indicator in v.get("visual_indicators", []):
            lookup[indicator.lower()] = v
        lookup[v["name"].lower()] = v
    return lookup


def log_result(finding):
    """
    Appends one finding (as produced by correlate.py's correlate()) to
    the inspection log. Unlike the old analyze_image()-driven version,
    the violation name and trade come straight from the finding itself —
    correlate.py already did the real work of matching spoken text
    against violations.json, so this just persists that result rather
    than re-deriving it from confidence scores.
    """
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    log_data = load_log()

    detections = finding.get("detections") or []
    top_confidence = max((d.get("confidence", 0) for d in detections), default=0)

    entry = {
        "id": len(log_data["inspections"]) + 1,
        "timestamp": finding.get("timestamp"),
        "object_name": finding.get("violation_name", ""),
        "confidence": top_confidence,
        "status": finding.get("status", ""),
        "finding_type": finding.get("finding_type"),
        "label": finding.get("label"),
        "trade_responsible": finding.get("trade_responsible"),
        "transcript_excerpt": finding.get("transcript_excerpt"),
        "image_path": finding.get("annotated_frame_path", ""),
        "detections": detections,
    }

    log_data["inspections"].append(entry)
    save_log(log_data)

    print(f"  Logged #{entry['id']} — {entry['object_name']} "
          f"[{entry['status']}/{entry['finding_type']}]")
    return entry


def view_log():
    log_data = load_log()

    if not log_data["inspections"]:
        print("No inspections logged yet.")
        return

    print(f"\n{'=' * 50}")
    print(f"  INSPECTION LOG — {len(log_data['inspections'])} entries")
    print(f"{'=' * 50}\n")

    for entry in log_data["inspections"]:
        eid = entry.get("id", "?")
        ts = entry.get("timestamp", "N/A")
        name = entry.get("object_name", entry.get("violation_name", "N/A"))
        desc = entry.get("description", "N/A")
        conf = entry.get("confidence", "N/A")
        urg = entry.get("urgency", entry.get("status", "N/A"))
        trade = entry.get("trade_responsible", "N/A") or "N/A"
        img = entry.get("image_path", "N/A")
        voice_note = entry.get("voice_note")
        video_path = entry.get("video_path")

        print(f"  #{eid}  {ts}")
        print(f"    Object:      {name}")
        print(f"    Description: {desc}")
        print(f"    Confidence:  {conf}%")
        print(f"    Urgency:     {urg}")
        print(f"    Trade:       {trade}")
        print(f"    Image:       {img}")
        if voice_note:
            print(f"    Voice Note:  {voice_note}")
        if video_path:
            print(f"    Video:       {video_path}")
        print()


if __name__ == "__main__":
    view_log()
