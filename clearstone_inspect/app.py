"""
Clearstone Inspect — local web dashboard.

Deferred batch-processing model: the inspector records a walkthrough on
their phone's native camera app and uploads it here (or drops it into
uploads/). The dashboard is an upload/progress/results interface —
there is no live camera or live voice session anymore. Every job is
processed one at a time by jobs.job_queue, and the dashboard's job/
findings/reports views all poll job-scoped JSON files under
jobs/<job_id>/ rather than any shared "current session" state.
"""

import glob
import os
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory, render_template
from werkzeug.utils import secure_filename

import ingest
from jobs import job_queue, read_status, JOBS_DIR
from utils import ensure_dir


app = Flask(__name__)

UPLOADS_DIR = "uploads"
ensure_dir(UPLOADS_DIR)
ensure_dir(JOBS_DIR)

STAGE_LABELS = {
    "queued": "Queued",
    "transcribing": "Transcribing walkthrough audio",
    "extracting": "Extracting frames",
    "detecting": "Analyzing frames for violations",
    "correlating": "Matching notes to visual evidence",
    "generating_report": "Building report",
    "complete": "Complete",
    "failed": "Failed",
}


# ---------------------------------------------------------------------
# Routes — pages
# ---------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/jobs/<job_id>/<path:filename>")
def serve_job_file(job_id, filename):
    """Serves job artifacts (annotated frames, the generated PDF, etc.)
    the same way /output/ used to serve the old shared output folder."""
    job_dir = os.path.join(JOBS_DIR, job_id)
    return send_from_directory(job_dir, filename)


# ---------------------------------------------------------------------
# Routes — JSON API
# ---------------------------------------------------------------------

@app.route("/api/upload", methods=["POST"])
def api_upload():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file was uploaded."}), 400

    upload = request.files["file"]
    if not upload.filename:
        return jsonify({"ok": False, "error": "No file was selected."}), 400

    project_name = request.form.get("project_name", "")
    address = request.form.get("address", "")
    inspector_name = request.form.get("inspector_name", "")

    filename = secure_filename(upload.filename)
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ingest.VIDEO_EXTENSIONS:
        return jsonify({
            "ok": False,
            "error": f"Unsupported file type '{ext}'. "
                     f"Expected one of: {sorted(ingest.VIDEO_EXTENSIONS)}",
        }), 400

    upload_path = os.path.join(UPLOADS_DIR, filename)
    print(f"[upload] Receiving {filename}...")
    upload.save(upload_path)

    job_id = ingest.accept_file(upload_path)
    if not job_id:
        return jsonify({"ok": False, "error": "Could not process the uploaded file."}), 400

    job_queue.enqueue(
        job_id,
        project_name=project_name,
        address=address,
        inspector_name=inspector_name,
        extra={
            "source_filename": filename,
            "project_name": project_name,
            "address": address,
            "inspector_name": inspector_name,
            "submitted_at": datetime.now().isoformat(),
        },
    )

    print(f"[upload] Job {job_id} created and enqueued for {filename}.")
    return jsonify({"ok": True, "job_id": job_id})


def _findings_summary(job_id):
    """Returns {fail, caution, pass} counts for a completed job, or None
    if findings.json doesn't exist yet."""
    findings_path = os.path.join(JOBS_DIR, job_id, "findings.json")
    if not os.path.exists(findings_path):
        return None

    import json
    with open(findings_path, "r") as f:
        data = json.load(f)

    findings = data.get("findings", [])
    statuses = [f.get("status", "PASS") for f in findings]
    return {
        "fail": statuses.count("FAIL"),
        "caution": statuses.count("CAUTION"),
        "pass": statuses.count("PASS"),
        "total": len(findings),
    }


@app.route("/api/jobs")
def api_jobs():
    job_ids = sorted(
        (name for name in os.listdir(JOBS_DIR) if os.path.isdir(os.path.join(JOBS_DIR, name))),
        reverse=True,
    )

    results = []
    for job_id in job_ids:
        status = read_status(job_id)
        status["stage_label"] = STAGE_LABELS.get(status.get("state"), status.get("state"))
        if status.get("state") == "complete":
            status["findings_summary"] = _findings_summary(job_id)
        results.append(status)

    return jsonify({"jobs": results})


@app.route("/api/jobs/<job_id>")
def api_job_detail(job_id):
    job_dir = os.path.join(JOBS_DIR, job_id)
    if not os.path.isdir(job_dir):
        return jsonify({"ok": False, "error": "No such job."}), 404

    status = read_status(job_id)
    status["stage_label"] = STAGE_LABELS.get(status.get("state"), status.get("state"))

    if status.get("state") == "complete":
        status["findings_summary"] = _findings_summary(job_id)
        report_path = status.get("report_path")
        if report_path and os.path.exists(report_path):
            rel = os.path.relpath(report_path, job_dir)
            status["report_url"] = f"/jobs/{job_id}/{rel.replace(os.sep, '/')}"

    return jsonify(status)


@app.route("/api/jobs/<job_id>/findings")
def api_job_findings(job_id):
    findings_path = os.path.join(JOBS_DIR, job_id, "findings.json")
    if not os.path.exists(findings_path):
        return jsonify({"ok": False, "error": "No findings for this job yet."}), 404

    import json
    with open(findings_path, "r") as f:
        data = json.load(f)

    # Rewrite each finding's annotated frame path into a servable URL.
    for finding in data.get("findings", []):
        frame_path = finding.get("annotated_frame_path")
        if frame_path and os.path.exists(frame_path):
            rel = os.path.relpath(frame_path, os.path.join(JOBS_DIR, job_id))
            finding["annotated_frame_url"] = f"/jobs/{job_id}/{rel.replace(os.sep, '/')}"
        else:
            finding["annotated_frame_url"] = None

    return jsonify(data)


@app.route("/api/jobs/<job_id>/transcript")
def api_job_transcript(job_id):
    transcript_path = os.path.join(JOBS_DIR, job_id, "transcript.json")
    if not os.path.exists(transcript_path):
        return jsonify({"ok": False, "error": "No transcript for this job yet."}), 404

    import json
    with open(transcript_path, "r") as f:
        data = json.load(f)

    return jsonify(data)


@app.route("/api/jobs/<job_id>/delete", methods=["POST"])
def api_job_delete(job_id):
    job_dir = os.path.join(JOBS_DIR, job_id)
    if not os.path.isdir(job_dir):
        return jsonify({"ok": False, "error": "No such job."}), 404

    import shutil
    shutil.rmtree(job_dir)
    print(f"[jobs] Job {job_id} deleted.")
    return jsonify({"ok": True})


@app.route("/api/reports")
def api_reports():
    """Lists every generated PDF report across all job directories,
    newest first, with the project name pulled from each job's status."""
    reports = []
    for pdf_path in glob.glob(os.path.join(JOBS_DIR, "*", "output", "*.pdf")):
        job_id = os.path.basename(os.path.dirname(os.path.dirname(pdf_path)))
        status = read_status(job_id)

        filename = os.path.basename(pdf_path)
        generated_at = datetime.fromtimestamp(os.path.getmtime(pdf_path))
        size_bytes = os.path.getsize(pdf_path)

        reports.append({
            "job_id": job_id,
            "filename": filename,
            "project_name": status.get("project_name", ""),
            "generated_at": generated_at.strftime("%Y-%m-%d %H:%M:%S"),
            "generated_at_sort": generated_at.isoformat(),
            "size_kb": round(size_bytes / 1024, 1),
            "url": f"/jobs/{job_id}/output/{filename}",
        })

    reports.sort(key=lambda r: r["generated_at_sort"], reverse=True)
    return jsonify({"reports": reports})


if __name__ == "__main__":
    print()
    print("=" * 50)
    print("  CLEARSTONE INSPECT — Web Dashboard")
    print("  Open http://localhost:5000 in your browser")
    print("=" * 50)
    print()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
