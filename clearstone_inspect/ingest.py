"""
Watches uploads/ for new walkthrough video files and turns each one into
a job: a per-job working directory at jobs/<job_id>/ containing the
video renamed to source.<ext>. Also exposes accept_file() so the
dashboard (or any other caller) can trigger ingest directly on upload,
without needing a file to land in the watched folder first.
"""

import os
import shutil
import time

from utils import ensure_dir, format_timestamp

UPLOADS_DIR = "uploads"
JOBS_DIR = "jobs"

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi"}

# How long a file's size must stay unchanged before we consider it fully
# written and safe to move — protects against picking up a video that's
# still mid-copy/mid-upload into uploads/.
STABLE_CHECK_SECONDS = 1.0


def _is_video_file(filename):
    ext = os.path.splitext(filename)[1].lower()
    return ext in VIDEO_EXTENSIONS


def _is_file_stable(path):
    """Returns True if the file's size hasn't changed across a short
    delay — a simple guard against ingesting a file that's still being
    written to uploads/."""
    try:
        size_before = os.path.getsize(path)
    except OSError:
        return False

    time.sleep(STABLE_CHECK_SECONDS)

    try:
        size_after = os.path.getsize(path)
    except OSError:
        return False

    return size_before == size_after and size_before > 0


def create_job(source_path):
    """
    Creates a new job for the given video file: makes jobs/<job_id>/,
    moves the file in as source<ext>, and returns the job_id.

    job_id is a timestamp string (YYYYMMDD_HHMMSS). If that exact job_id
    already exists (two files ingested within the same second), a
    numeric suffix is appended to keep it unique.
    """
    ext = os.path.splitext(source_path)[1].lower()
    if ext not in VIDEO_EXTENSIONS:
        raise ValueError(
            f"Unsupported video extension '{ext}'. "
            f"Expected one of: {sorted(VIDEO_EXTENSIONS)}"
        )

    job_id = format_timestamp()
    job_dir = os.path.join(JOBS_DIR, job_id)
    suffix = 1
    while os.path.exists(job_dir):
        job_id = f"{format_timestamp()}_{suffix}"
        job_dir = os.path.join(JOBS_DIR, job_id)
        suffix += 1

    ensure_dir(job_dir)

    dest_path = os.path.join(job_dir, f"source{ext}")
    shutil.move(source_path, dest_path)

    print(f"[ingest] Job {job_id} created: {dest_path}")
    return job_id


def accept_file(file_path):
    """
    Accepts a file path directly and turns it into a job — the entry
    point the dashboard (or any other caller) should use when a file is
    uploaded through the UI rather than dropped into uploads/.

    Returns the new job_id, or None if the file isn't a supported video.
    """
    if not os.path.exists(file_path):
        print(f"[ingest] File not found: {file_path}")
        return None

    if not _is_video_file(file_path):
        print(f"[ingest] Not a supported video file: {file_path}")
        return None

    return create_job(file_path)


def scan_uploads_once():
    """
    Scans uploads/ once for new, stable video files and creates a job
    for each one found. Returns the list of job_ids created.
    """
    ensure_dir(UPLOADS_DIR)

    created = []
    for filename in sorted(os.listdir(UPLOADS_DIR)):
        path = os.path.join(UPLOADS_DIR, filename)
        if not os.path.isfile(path) or not _is_video_file(filename):
            continue

        if not _is_file_stable(path):
            print(f"[ingest] Skipping {filename} — still being written.")
            continue

        try:
            job_id = create_job(path)
            created.append(job_id)
        except Exception as exc:
            print(f"[ingest] ERROR ingesting {filename}: {exc}")

    return created


def watch_uploads(poll_seconds=3.0, on_job_created=None, stop_event=None):
    """
    Continuously polls uploads/ for new video files, turning each one
    into a job. Blocks forever (run this in a background thread) unless
    stop_event (a threading.Event) is provided and gets set.

    on_job_created: optional callback invoked with each new job_id as
    soon as it's created — lets a caller (e.g. jobs.py) enqueue it for
    processing immediately instead of polling job state separately.
    """
    ensure_dir(UPLOADS_DIR)
    print(f"[ingest] Watching '{UPLOADS_DIR}/' for new video files "
          f"({', '.join(sorted(VIDEO_EXTENSIONS))})...")

    while True:
        if stop_event is not None and stop_event.is_set():
            print("[ingest] Stop requested — no longer watching uploads/.")
            return

        for job_id in scan_uploads_once():
            if on_job_created:
                try:
                    on_job_created(job_id)
                except Exception as exc:
                    print(f"[ingest] on_job_created callback error: {exc}")

        time.sleep(poll_seconds)


if __name__ == "__main__":
    watch_uploads()
