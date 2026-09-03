"""
Orchestrates the full deferred-processing sequence for one job:

    transcribe -> markers -> extract -> detect -> correlate -> report

ingest.py has already run by the time a job reaches this pipeline (it's
what creates the job in the first place); this module picks up from
transcription onward. Called by jobs.py, which wraps each stage with
state/progress tracking — this module itself has no notion of job
state, it just runs the stages in order and returns the result.
"""

import os

from transcribe import transcribe_job
from markers import find_markers
from extract import extract_frames
from detect import detect_frames
from correlate import correlate
from logger import log_result
from report import generate_report


def run_pipeline(job_id, project_name="", address="", inspector_name="", notes="",
                  on_stage=None):
    """
    Runs every stage of the pipeline for job_id in order. Returns a dict
    summarizing what was produced at each stage: segments, markers,
    frames, detections (frames with detections attached), findings, and
    report_path.

    on_stage: optional callback invoked with the stage name (a string
    matching jobs.py's state machine stages) right before each stage
    starts, so a caller can track/persist progress without this module
    needing to know anything about job state itself.
    """

    def _announce(stage):
        print(f"[pipeline] Job {job_id}: stage '{stage}'")
        if on_stage:
            on_stage(stage)

    _announce("transcribing")
    segments = transcribe_job(job_id)

    _announce("extracting")
    markers = find_markers(segments=segments)
    frames = extract_frames(job_id, markers=markers)

    _announce("detecting")
    frames_with_detections = detect_frames(job_id, frames)

    _announce("correlating")
    findings = correlate(job_id, markers, frames_with_detections)

    for finding in findings:
        log_result(finding)

    _announce("generating_report")
    report_path = generate_report(job_id, project_name, address, inspector_name, notes)

    return {
        "job_id": job_id,
        "segments": segments,
        "markers": markers,
        "frames": frames_with_detections,
        "findings": findings,
        "report_path": report_path,
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python pipeline.py <job_id> [project_name] [address] [inspector_name]")
        sys.exit(1)

    job_id = sys.argv[1]
    project_name = sys.argv[2] if len(sys.argv) > 2 else "Test Project"
    address = sys.argv[3] if len(sys.argv) > 3 else ""
    inspector_name = sys.argv[4] if len(sys.argv) > 4 else ""

    result = run_pipeline(job_id, project_name, address, inspector_name)
    print(f"\n[pipeline] Done. {len(result['findings'])} finding(s). "
          f"Report: {result['report_path']}")
