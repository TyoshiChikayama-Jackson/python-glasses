import cv2
import os
from datetime import datetime


OUTPUT_DIR = "output"


def ensure_dir(path):
    """Creates a directory (and any missing parents) if it doesn't
    already exist. Returns the path, so it can be used inline:
    frames_dir = ensure_dir(os.path.join(job_dir, "frames"))"""
    os.makedirs(path, exist_ok=True)
    return path


def generate_filename(prefix, extension):
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(OUTPUT_DIR, f"{prefix}_{timestamp}.{extension}")


def format_timestamp(dt=None):
    """Returns a sortable timestamp string (YYYYMMDD_HHMMSS), suitable
    for use as a job_id or embedding in a filename. Defaults to now."""
    dt = dt or datetime.now()
    return dt.strftime("%Y%m%d_%H%M%S")


def seconds_to_mmss(seconds):
    """Converts a float/int number of seconds into an "MM:SS" string for
    transcript/timeline display (e.g. 125.4 -> "02:05"). Negative values
    are clamped to 0."""
    total_seconds = max(0, int(round(seconds)))
    minutes, secs = divmod(total_seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


def extract_video_thumbnail(video_path):
    """
    Extracts the first clear (non-black) frame from a saved video file and
    saves it as a thumbnail jpg alongside it, using the same timestamp as
    the video filename (e.g. video_20260828_101500.avi ->
    video_20260828_101500_thumb.jpg).

    Returns the thumbnail path, or None on failure.
    """
    if not os.path.exists(video_path):
        print(f"Error: Video not found for thumbnail — {video_path}")
        return None

    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"Error: Could not open video for thumbnail — {video_path}")
        return None

    frame = None
    for _ in range(30):  # scan up to the first ~30 frames
        ret, candidate = cap.read()
        if not ret or candidate is None:
            break
        if candidate.max() > 10:
            frame = candidate
            break

    cap.release()

    if frame is None:
        print(f"Warning: Could not find a clear frame in {video_path}.")
        return None

    base = os.path.splitext(os.path.basename(video_path))[0]
    thumb_path = os.path.join(OUTPUT_DIR, f"{base}_thumb.jpg")
    cv2.imwrite(thumb_path, frame)

    print(f"Video thumbnail saved: {thumb_path}")
    return thumb_path
