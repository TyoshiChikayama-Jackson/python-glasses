"""
A simple job queue and state machine for the deferred-processing
pipeline. Jobs are processed one at a time in a background thread;
progress and current stage are persisted to jobs/<job_id>/status.json
so the dashboard (or any other caller) can poll it without needing to
share process memory with the worker thread.
"""

import json
import os
import queue
import threading
import time

from pipeline import run_pipeline
from utils import ensure_dir

JOBS_DIR = "jobs"

STATES = [
    "queued",
    "transcribing",
    "extracting",
    "detecting",
    "correlating",
    "generating_report",
    "complete",
    "failed",
]

# Rough progress percentage at the *start* of each stage — used so
# status.json always has a reasonable number even mid-stage, without
# needing every stage to report fine-grained sub-progress itself.
STAGE_PROGRESS = {
    "queued": 0,
    "transcribing": 10,
    "extracting": 35,
    "detecting": 50,
    "correlating": 80,
    "generating_report": 90,
    "complete": 100,
    "failed": 0,
}


def _status_path(job_id):
    return os.path.join(JOBS_DIR, job_id, "status.json")


def write_status(job_id, state, progress=None, error=None, extra=None):
    """
    Persists the current state/progress for a job to
    jobs/<job_id>/status.json. progress defaults to STAGE_PROGRESS[state]
    if not given explicitly.

    Merges into whatever status.json already has rather than replacing
    it outright — metadata written once (source_filename, project_name,
    etc., from the upload) needs to survive every later stage
    transition, not just the call that set it.
    """
    ensure_dir(os.path.join(JOBS_DIR, job_id))

    status = read_status(job_id)
    if status.get("state") == "unknown":
        status = {"job_id": job_id}

    status["state"] = state
    status["progress"] = progress if progress is not None else STAGE_PROGRESS.get(state, 0)
    status["updated_at"] = time.time()
    if error:
        status["error"] = error
    if extra:
        status.update(extra)

    with open(_status_path(job_id), "w") as f:
        json.dump(status, f, indent=2)

    print(f"[jobs] Job {job_id}: state={state} progress={status['progress']}%")


def read_status(job_id):
    """Returns the persisted status dict for a job, or a default
    'unknown' status if none has been written yet."""
    path = _status_path(job_id)
    if not os.path.exists(path):
        return {"job_id": job_id, "state": "unknown", "progress": 0}
    with open(path, "r") as f:
        return json.load(f)


class JobQueue:
    """
    Owns a single background worker thread that pulls job requests off a
    queue and runs the full pipeline for each one, one at a time —
    processing jobs sequentially rather than in parallel keeps GPU/CPU
    load predictable and avoids two jobs fighting over the same YOLO
    model instance.
    """

    def __init__(self):
        self._queue = queue.Queue()
        self._thread = None
        self._lock = threading.Lock()

    def enqueue(self, job_id, project_name="", address="", inspector_name="", notes="",
                extra=None):
        # Write display metadata (source filename, project name, etc.)
        # into status.json in this same first write — write_status()
        # merges on every later call, but the *first* write has to
        # already contain this or a fast-moving background worker could
        # overwrite status.json with a later stage before a second,
        # separate metadata write ever lands.
        write_status(job_id, "queued", extra=extra)
        self._queue.put({
            "job_id": job_id,
            "project_name": project_name,
            "address": address,
            "inspector_name": inspector_name,
            "notes": notes,
        })
        self._ensure_worker()
        print(f"[jobs] Job {job_id} enqueued.")

    def _ensure_worker(self):
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._thread = threading.Thread(target=self._run_worker, daemon=True)
            self._thread.start()

    def _run_worker(self):
        while True:
            try:
                job_request = self._queue.get(timeout=1.0)
            except queue.Empty:
                # Nothing left to process right now — exit; enqueue()
                # will spin up a fresh worker thread next time a job
                # comes in.
                return

            self._process_job(job_request)
            self._queue.task_done()

    def _process_job(self, job_request):
        job_id = job_request["job_id"]

        def on_stage(stage):
            write_status(job_id, stage)

        try:
            result = run_pipeline(
                job_id,
                project_name=job_request.get("project_name", ""),
                address=job_request.get("address", ""),
                inspector_name=job_request.get("inspector_name", ""),
                notes=job_request.get("notes", ""),
                on_stage=on_stage,
            )
            write_status(job_id, "complete", extra={
                "report_path": result.get("report_path"),
                "finding_count": len(result.get("findings", [])),
            })
        except Exception as exc:
            import traceback
            print(f"[jobs] Job {job_id} FAILED: {exc}")
            traceback.print_exc()
            write_status(job_id, "failed", error=str(exc))


job_queue = JobQueue()


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python jobs.py <job_id> [project_name] [address] [inspector_name]")
        sys.exit(1)

    job_id = sys.argv[1]
    project_name = sys.argv[2] if len(sys.argv) > 2 else "Test Project"
    address = sys.argv[3] if len(sys.argv) > 3 else ""
    inspector_name = sys.argv[4] if len(sys.argv) > 4 else ""

    job_queue.enqueue(job_id, project_name, address, inspector_name)

    # Block here just so the standalone CLI test has something to watch
    # rather than exiting immediately while the background thread is
    # still working.
    while True:
        status = read_status(job_id)
        print(f"  state={status['state']} progress={status['progress']}%")
        if status["state"] in ("complete", "failed"):
            break
        time.sleep(1.0)
