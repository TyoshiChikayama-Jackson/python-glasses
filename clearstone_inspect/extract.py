"""
Extracts frames from a job's source video: a baseline sweep (one frame
every 5 seconds) plus targeted frames around every marker found by
markers.py (marker_time - 3s, marker_time, marker_time + 3s).
"""

import os

import cv2

from utils import ensure_dir

JOBS_DIR = "jobs"
BASELINE_INTERVAL_SECONDS = 5.0
MARKER_OFFSET_SECONDS = 3.0


def _find_source_video(job_dir):
    for filename in os.listdir(job_dir):
        if filename.startswith("source."):
            return os.path.join(job_dir, filename)
    return None


def _grab_frame_at(cap, timestamp_seconds, fps, frame_count):
    """
    Seeks to the given timestamp and reads one frame. Clamps the
    timestamp into the video's actual duration so out-of-range marker
    offsets (e.g. marker_time - 3s on a marker at t=1s) don't fail.
    Returns (frame, actual_timestamp_used) or (None, None) on failure.
    """
    duration = frame_count / fps if fps else 0
    clamped = max(0.0, min(timestamp_seconds, max(duration - 0.01, 0.0)))

    cap.set(cv2.CAP_PROP_POS_MSEC, clamped * 1000.0)
    ret, frame = cap.read()
    if not ret or frame is None:
        return None, None

    return frame, clamped


def extract_frames(job_id, markers=None):
    """
    Extracts a baseline sweep (every BASELINE_INTERVAL_SECONDS) plus
    three frames per marker (marker - 3s, marker, marker + 3s) from
    jobs/<job_id>/source.<ext>, saving each to
    jobs/<job_id>/frames/frame_<seconds>.jpg.

    markers: optional list of marker dicts (from markers.find_markers),
    each needing at least a "timestamp" key. If omitted, only the
    baseline sweep is extracted.

    Returns a list of {path, timestamp, source} where source is
    "baseline" or "marker". Frames that land at (numerically) the same
    timestamp — e.g. a baseline tick that coincides with a marker
    offset — are only saved once, tagged "marker" (marker frames take
    precedence since they're the ones correlate.py actually needs).
    """
    job_dir = os.path.join(JOBS_DIR, job_id)
    if not os.path.isdir(job_dir):
        raise FileNotFoundError(f"No such job directory: {job_dir}")

    source_path = _find_source_video(job_dir)
    if source_path is None:
        raise FileNotFoundError(f"No source video found in {job_dir}")

    cap = cv2.VideoCapture(source_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {source_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 0
    frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    duration = (frame_count / fps) if fps else 0

    if duration <= 0:
        cap.release()
        raise RuntimeError(
            f"Could not determine duration of {source_path} "
            f"(fps={fps}, frame_count={frame_count})."
        )

    print(f"[extract] Job {job_id}: video duration ~{duration:.1f}s, "
          f"fps={fps:.1f}")

    frames_dir = ensure_dir(os.path.join(job_dir, "frames"))

    # Build the full set of timestamps to grab, tagging each with its
    # source. Marker timestamps are added after the baseline sweep and
    # take precedence on collision (see the dict keyed by rounded
    # timestamp below).
    wanted = {}  # rounded_timestamp -> source label

    t = 0.0
    while t < duration:
        wanted[round(t, 1)] = "baseline"
        t += BASELINE_INTERVAL_SECONDS

    for marker in (markers or []):
        marker_time = marker["timestamp"]
        for offset in (-MARKER_OFFSET_SECONDS, 0.0, MARKER_OFFSET_SECONDS):
            candidate = marker_time + offset
            if candidate < 0:
                continue
            wanted[round(candidate, 1)] = "marker"

    results = []
    for timestamp in sorted(wanted.keys()):
        source_label = wanted[timestamp]
        frame, actual_ts = _grab_frame_at(cap, timestamp, fps, frame_count)
        if frame is None:
            print(f"[extract] Job {job_id}: could not read frame at "
                  f"{timestamp:.1f}s — skipping.")
            continue

        # Name the file after the timestamp we actually landed on (after
        # clamping), formatted with one decimal so distinct nearby
        # requests don't collide on disk.
        frame_filename = f"frame_{actual_ts:.1f}.jpg"
        frame_path = os.path.join(frames_dir, frame_filename)
        cv2.imwrite(frame_path, frame)

        results.append({
            "path": frame_path,
            "timestamp": actual_ts,
            "source": source_label,
        })

    cap.release()

    print(f"[extract] Job {job_id}: saved {len(results)} frame(s) "
          f"({sum(1 for r in results if r['source'] == 'baseline')} baseline, "
          f"{sum(1 for r in results if r['source'] == 'marker')} marker).")

    return results


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python extract.py <job_id>")
        sys.exit(1)

    for r in extract_frames(sys.argv[1]):
        print(r)
