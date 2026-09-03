"""
The core of the batch-processing restructure: matches spoken
observations (markers.py) against visual evidence (detect.py's per-frame
detections) and produces findings.

Critically, a finding's violation NAME comes from matching the
inspector's spoken words against violations.json — never from picking
whichever violations.json entry happens to have the highest max
detection confidence across the board (the old analyze_image() bug,
where every single detection got labeled "Exposed Electrical Wiring"
regardless of what was actually seen). If nothing was said, the
"violation name" for an UNMENTIONED finding is just the detected
object's own class name — it isn't forced to match a KB entry that
was never actually mentioned or otherwise implicated.
"""

import json
import os

CONSTRUCTION_RELEVANT_CLASSES = {
    "cable",
    "power plugs and sockets",
    "ladder",
    "drill",
    "hammer",
    "screwdriver",
    "wrench",
    "nail",
    "tool",
    "helmet",
    "glove",
}

MARKER_WINDOW_SECONDS = 3.0
UNMENTIONED_WINDOW_SECONDS = 10.0
UNMENTIONED_CONFIDENCE_THRESHOLD = 60.0


def _load_violations(violations_path="violations.json"):
    if not os.path.exists(violations_path):
        return []
    with open(violations_path, "r") as f:
        return json.load(f).get("violations", [])


def match_violation_from_text(text, violations):
    """
    Matches spoken text against violations.json by name or
    visual_indicators — this is the ONLY source of a finding's violation
    name; detection confidence never determines it. Returns the matched
    violation dict, or None if nothing in the text matches any KB entry.
    """
    text_lower = text.lower()

    for violation in violations:
        if violation["name"].lower() in text_lower:
            return violation
        for indicator in violation.get("visual_indicators", []):
            if indicator.lower() in text_lower:
                return violation

    return None


def _is_construction_relevant(detection):
    return detection["class"].lower() in CONSTRUCTION_RELEVANT_CLASSES


def _frames_within_window(frames, center_timestamp, window_seconds):
    return [
        f for f in frames
        if abs(f["timestamp"] - center_timestamp) <= window_seconds
    ]


def _best_annotated_frame(job_dir, frames_in_window):
    """
    Picks the frame (within a window) with the single highest-confidence
    detection, and returns its annotated-copy path if one exists (a
    frame with zero detections never got an annotated copy written by
    detect.py), else its plain frame path.
    """
    if not frames_in_window:
        return None

    best_frame = None
    best_confidence = -1.0

    for frame in frames_in_window:
        for det in frame.get("detections", []):
            if det["confidence"] > best_confidence:
                best_confidence = det["confidence"]
                best_frame = frame

    if best_frame is None:
        # No detections in the window at all — just return the frame
        # closest to the window's center as a fallback reference image.
        best_frame = frames_in_window[0]

    filename = os.path.basename(best_frame["path"])
    annotated_path = os.path.join(job_dir, "annotated", filename)
    if os.path.exists(annotated_path):
        return annotated_path
    return best_frame["path"]


def _all_detections_in_window(frames_in_window):
    all_detections = []
    for frame in frames_in_window:
        all_detections.extend(frame.get("detections", []))
    return all_detections


def correlate_marker(job_dir, marker, frames, violations):
    """
    Produces one finding for a single marker: looks at detections on
    frames within MARKER_WINDOW_SECONDS of the marker, and classifies it
    as CONFIRMED or UNCONFIRMED.
    """
    window_frames = _frames_within_window(
        frames, marker["timestamp"], MARKER_WINDOW_SECONDS
    )
    detections = _all_detections_in_window(window_frames)
    relevant_detections = [d for d in detections if _is_construction_relevant(d)]

    matched_violation = match_violation_from_text(marker["text"], violations)
    annotated_frame_path = _best_annotated_frame(job_dir, window_frames)

    if relevant_detections:
        finding_type = "confirmed"
        status = "FAIL" if matched_violation else "CAUTION"
        label = None
    else:
        finding_type = "unconfirmed"
        status = "CAUTION"
        label = "stated, not visually confirmed"

    if matched_violation:
        violation_name = matched_violation["name"]
        trade = matched_violation.get("trade_responsible")
    else:
        # Nothing in violations.json matched the spoken text — name the
        # finding after whatever was actually detected instead of
        # forcing it onto an unrelated KB entry.
        violation_name = (
            relevant_detections[0]["class"] if relevant_detections
            else "Unspecified issue"
        )
        trade = None

    return {
        "violation_name": violation_name,
        "transcript_excerpt": marker["text"],
        "timestamp": marker["timestamp"],
        "annotated_frame_path": annotated_frame_path,
        "detections": detections,
        "status": status,
        "finding_type": finding_type,
        "label": label,
        "trade_responsible": trade,
        "marker_type": marker["type"],
        "marker_weight": marker["weight"],
    }


def correlate_unmentioned(job_dir, baseline_frame, frames, markers):
    """
    Produces an UNMENTIONED finding for a baseline frame that has a
    construction-relevant detection above UNMENTIONED_CONFIDENCE_THRESHOLD
    but no marker within UNMENTIONED_WINDOW_SECONDS of it.
    """
    detections = baseline_frame.get("detections", [])
    relevant_detections = [
        d for d in detections
        if _is_construction_relevant(d) and d["confidence"] > UNMENTIONED_CONFIDENCE_THRESHOLD
    ]
    if not relevant_detections:
        return None

    has_nearby_marker = any(
        abs(m["timestamp"] - baseline_frame["timestamp"]) <= UNMENTIONED_WINDOW_SECONDS
        for m in markers
    )
    if has_nearby_marker:
        return None

    filename = os.path.basename(baseline_frame["path"])
    annotated_path = os.path.join(job_dir, "annotated", filename)
    frame_path = annotated_path if os.path.exists(annotated_path) else baseline_frame["path"]

    top_detection = max(relevant_detections, key=lambda d: d["confidence"])

    return {
        "violation_name": top_detection["class"],
        "transcript_excerpt": None,
        "timestamp": baseline_frame["timestamp"],
        "annotated_frame_path": frame_path,
        "detections": detections,
        "status": "CAUTION",
        "finding_type": "unmentioned",
        "label": "detected, not mentioned",
        "trade_responsible": None,
        "marker_type": None,
        "marker_weight": 0,
    }


def correlate(job_id, markers, frames, violations_path="violations.json"):
    """
    Produces the full list of findings for a job: one per marker
    (CONFIRMED/UNCONFIRMED), plus one per unmentioned baseline detection.

    markers: list of marker dicts from markers.find_markers().
    frames: list of {path, timestamp, source, detections} from
            detect.detect_frames().

    Returns the findings list and also writes it to
    jobs/<job_id>/findings.json.
    """
    job_dir = os.path.join("jobs", job_id)
    violations = _load_violations(violations_path)

    findings = []

    for marker in markers:
        findings.append(correlate_marker(job_dir, marker, frames, violations))

    baseline_frames = [f for f in frames if f.get("source") == "baseline"]
    for frame in baseline_frames:
        finding = correlate_unmentioned(job_dir, frame, frames, markers)
        if finding:
            findings.append(finding)

    findings.sort(key=lambda f: f["timestamp"])

    findings_path = os.path.join(job_dir, "findings.json")
    with open(findings_path, "w") as f:
        json.dump({"job_id": job_id, "findings": findings}, f, indent=2)

    print(f"[correlate] Job {job_id}: {len(findings)} finding(s) saved to "
          f"{findings_path}")

    return findings


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python correlate.py <job_id>")
        sys.exit(1)

    job_id = sys.argv[1]
    job_dir = os.path.join("jobs", job_id)

    with open(os.path.join(job_dir, "transcript.json")) as f:
        transcript = json.load(f)

    from markers import find_markers
    markers_list = find_markers(segments=transcript["segments"])

    # Expects detect.py to have already been run and its output cached;
    # for standalone testing, this just re-detects on the existing
    # frames/ directory with source inferred from filenames.
    frames_dir = os.path.join(job_dir, "frames")
    frame_list = []
    for filename in sorted(os.listdir(frames_dir)):
        if filename.endswith(".jpg"):
            timestamp = float(filename.replace("frame_", "").replace(".jpg", ""))
            frame_list.append({
                "path": os.path.join(frames_dir, filename),
                "timestamp": timestamp,
                "source": "baseline",
                "detections": [],
            })

    result = correlate(job_id, markers_list, frame_list)
    print(json.dumps(result, indent=2))
