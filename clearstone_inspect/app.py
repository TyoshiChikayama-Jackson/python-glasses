"""
Clearstone Inspect — local web dashboard.

Deferred batch-processing model: the inspector records a walkthrough on
their phone's native camera app and drops the file into a watched
folder. All analysis happens afterward — nothing runs live. This module
currently keeps the Flask app skeleton (session state, voice listener
supervisor, reports library, session management, PDF report generation)
while the live camera/capture/recording pieces have been removed as
part of that restructure.
"""

import glob
import os
import re
import threading
import time
from datetime import datetime

from flask import Flask, jsonify, request, send_from_directory, render_template

from logger import load_log, archive_and_clear_log, delete_all_inspections
from report import generate_report
from voice import listen_for_notes


app = Flask(__name__)

OUTPUT_DIR = "output"
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ---------------------------------------------------------------------
# Shared session state
# ---------------------------------------------------------------------

class SessionState:
    def __init__(self):
        self.lock = threading.Lock()
        self.status = "Idle"          # Idle, Inspecting, Recording, Error
        self.mic_status = "Off"       # Listening, Off
        self.session_detections = 0
        self.voice_thread_started = False
        self.voice_thread_should_run = False  # explicit user intent, survives thread death
        self.last_report_path = None
        self.session_start_time = None  # set when inspection starts

    def set_status(self, value):
        with self.lock:
            self.status = value

    def set_mic_status(self, value):
        with self.lock:
            self.mic_status = value

    def bump_detections(self):
        with self.lock:
            self.session_detections += 1

    def start_session_timer(self):
        with self.lock:
            if self.session_start_time is None:
                self.session_start_time = time.time()

    def snapshot(self):
        with self.lock:
            elapsed = (
                time.time() - self.session_start_time
                if self.session_start_time
                else 0
            )
            # Reflect the voice thread's *actual* liveness here, not
            # just the last value someone set — this is what makes the
            # mic indicator accurate even if the thread died silently
            # between watchdog checks.
            actual_mic_status = "Listening" if voice_listener.is_alive() else "Off"
            return {
                "status": self.status,
                "mic_status": actual_mic_status,
                "session_detections": self.session_detections,
                "wake_word": "Petra",
                "session_seconds": int(elapsed),
            }


state = SessionState()


# ---------------------------------------------------------------------
# Live transcription state (for the dashboard's transcription bar)
# ---------------------------------------------------------------------

class TranscriptionState:
    """
    Tracks the live wake-word/transcription lifecycle so the dashboard
    can show a real-time "Listening... / Processing... / <text>" bar.

    States: idle, listening, processing, complete.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.status = "idle"
        self.text = ""
        self.completed_at = None
        self.AUTO_IDLE_SECONDS = 5.0

    def set_listening(self):
        with self.lock:
            self.status = "listening"
            self.text = ""
            self.completed_at = None

    def set_processing(self):
        with self.lock:
            self.status = "processing"

    def set_complete(self, text):
        with self.lock:
            self.status = "complete"
            self.text = text
            self.completed_at = time.time()

    def set_idle(self):
        with self.lock:
            self.status = "idle"
            self.text = ""
            self.completed_at = None

    def snapshot(self):
        with self.lock:
            # Auto-return to idle 5 seconds after completion so the
            # dashboard's transcription bar fades out on its own.
            if (
                self.status == "complete"
                and self.completed_at is not None
                and time.time() - self.completed_at >= self.AUTO_IDLE_SECONDS
            ):
                self.status = "idle"
                self.text = ""
                self.completed_at = None

            return {"status": self.status, "text": self.text}


transcription_state = TranscriptionState()


# ---------------------------------------------------------------------
# Voice listener worker
# ---------------------------------------------------------------------

def on_wake_word_detected():
    print("\n" + "=" * 50)
    print('  [PETRA] Wake word "Petra" detected — listening for note...')
    print("=" * 50)
    transcription_state.set_listening()


def on_transcribing_started():
    print("  [PETRA] Transcribing captured audio with Whisper...")
    transcription_state.set_processing()


def on_voice_note_captured(note_text):
    print(f'  [PETRA] Transcription complete: "{note_text}"')
    transcription_state.set_complete(note_text)


class VoiceListenerSupervisor:
    """
    Owns the background thread running voice.listen_for_notes and keeps
    it alive for the lifetime of the app session — surviving tab
    navigation, report generation, and session management actions.

    If the underlying thread dies for any reason (an unhandled
    exception, a crashed audio device, etc.) while voice_thread_should_run
    is still True, a watchdog restarts it automatically rather than
    leaving Petra silently dead until the next full app restart. The
    listener only stops for good when explicitly told to via stop()
    (e.g. an explicit Stop Inspection action) — never as a side effect
    of Reports tab navigation, Clear Session, or report generation.
    """

    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self._watchdog_started = False

    def is_alive(self):
        return self.thread is not None and self.thread.is_alive()

    def start(self):
        with self.lock:
            state.voice_thread_should_run = True

            if self.is_alive():
                print("[voice] start() called but listener thread is "
                      "already alive — leaving it running.")
                return

            self._spawn_thread()
            self._ensure_watchdog()

    def stop(self):
        # There is no way to interrupt voice.listen_for_notes() cleanly
        # mid-blocking-call (it has no cancellation hook), so "stop"
        # here means: stop treating an unexpected death as something to
        # auto-restart, and reflect that in mic_status. The daemon
        # thread itself will end when the process exits.
        with self.lock:
            state.voice_thread_should_run = False
            state.set_mic_status("Off")
            transcription_state.set_idle()
            print("[voice] Stop requested — listener will not be "
                  "auto-restarted if it ends.")

    def _spawn_thread(self):
        state.voice_thread_started = True
        state.set_mic_status("Listening")

        def _run():
            try:
                listen_for_notes(
                    on_voice_note_captured,
                    on_wake_detected=on_wake_word_detected,
                    on_transcribing_start=on_transcribing_started,
                )
            except Exception as exc:
                import traceback
                print(f"[voice] Listener crashed: {exc}")
                traceback.print_exc()
            finally:
                print("[voice] Listener thread has exited "
                      f"(should_run={state.voice_thread_should_run}).")

        self.thread = threading.Thread(target=_run, daemon=True)
        self.thread.start()
        print(f"[voice] Listener thread started (alive={self.thread.is_alive()}).")

    def _ensure_watchdog(self):
        if self._watchdog_started:
            return
        self._watchdog_started = True

        def _watch():
            while True:
                time.sleep(2.0)
                with self.lock:
                    if not state.voice_thread_should_run:
                        # User explicitly stopped it — nothing to watch for.
                        continue
                    if not self.is_alive():
                        print("[voice] WATCHDOG: listener thread is dead "
                              "but should be running — restarting it.")
                        self._spawn_thread()
                    else:
                        state.set_mic_status("Listening")

        watchdog_thread = threading.Thread(target=_watch, daemon=True)
        watchdog_thread.start()


voice_listener = VoiceListenerSupervisor()


# ---------------------------------------------------------------------
# Routes — pages
# ---------------------------------------------------------------------

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/output/<path:filename>")
def serve_output_file(filename):
    return send_from_directory(OUTPUT_DIR, filename)


# ---------------------------------------------------------------------
# Routes — JSON API
# ---------------------------------------------------------------------

@app.route("/api/status")
def api_status():
    return jsonify(state.snapshot())


@app.route("/api/transcription")
def api_transcription():
    return jsonify(transcription_state.snapshot())


@app.route("/api/voice/status")
def api_voice_status():
    alive = voice_listener.is_alive()
    label = request.args.get("label", "")
    if label:
        print(f"[voice] STATUS CHECK ({label}): thread alive={alive}, "
              f"should_run={state.voice_thread_should_run}")
    return jsonify({
        "alive": alive,
        "should_run": state.voice_thread_should_run,
        "mic_status": "Listening" if alive else "Off",
    })


@app.route("/api/log")
def api_log():
    log_data = load_log()
    return jsonify(log_data)


@app.route("/api/report", methods=["POST"])
def api_report():
    payload = request.get_json(silent=True) or {}
    project_name = payload.get("project_name", "")
    address = payload.get("address", "")
    inspector_name = payload.get("inspector_name", "")
    notes = payload.get("notes", "")

    print("[report] Generate Report clicked.")
    try:
        report_path = generate_report(project_name, address, inspector_name, notes)
    except Exception as exc:
        print(f"[report] UNEXPECTED ERROR: {exc}")
        return jsonify({"ok": False, "error": f"Unexpected error: {exc}"}), 500

    if not report_path:
        print("[report] No inspections to report.")
        return jsonify({"ok": False, "error": "No inspections to report."}), 400

    state.last_report_path = report_path
    filename = os.path.basename(report_path)
    print(f"[report] Report generated: {report_path}")
    return jsonify({
        "ok": True,
        "download_url": f"/output/{filename}",
        "view_url": f"/output/{filename}",
    })


# Matches report filenames generated by report.py, e.g.
# "inspection_report_2026-09-02.pdf" -> date "2026-09-02".
_REPORT_FILENAME_RE = re.compile(r"inspection_report_(\d{4}-\d{2}-\d{2})")


@app.route("/api/reports")
def api_reports():
    pdf_paths = glob.glob(os.path.join(OUTPUT_DIR, "*.pdf"))

    reports = []
    for path in pdf_paths:
        filename = os.path.basename(path)

        match = _REPORT_FILENAME_RE.search(filename)
        if match:
            try:
                generated_at = datetime.strptime(match.group(1), "%Y-%m-%d")
            except ValueError:
                generated_at = datetime.fromtimestamp(os.path.getmtime(path))
        else:
            # Fall back to the file's modified time if the filename
            # doesn't match the expected pattern (e.g. renamed by hand).
            generated_at = datetime.fromtimestamp(os.path.getmtime(path))

        size_bytes = os.path.getsize(path)

        reports.append({
            "filename": filename,
            "generated_at": generated_at.strftime("%Y-%m-%d %H:%M:%S"),
            "generated_at_sort": generated_at.isoformat(),
            "size_kb": round(size_bytes / 1024, 1),
            "url": f"/output/{filename}",
        })

    reports.sort(key=lambda r: r["generated_at_sort"], reverse=True)
    return jsonify({"reports": reports})


@app.route("/api/session/new", methods=["POST"])
def api_session_new():
    archive_path = archive_and_clear_log()
    with state.lock:
        state.session_detections = 0
    return jsonify({"ok": True, "archived_to": archive_path})


@app.route("/api/session/continue", methods=["POST"])
def api_session_continue():
    return jsonify({"ok": True})


@app.route("/api/session/delete", methods=["POST"])
def api_session_delete():
    payload = request.get_json(silent=True) or {}
    if payload.get("confirm") != "DELETE":
        return jsonify({"ok": False, "error": 'Type "DELETE" to confirm.'}), 400

    removed = delete_all_inspections()
    with state.lock:
        state.session_detections = 0
    return jsonify({"ok": True, "removed": removed})


if __name__ == "__main__":
    print()
    print("=" * 50)
    print("  CLEARSTONE INSPECT — Web Dashboard")
    print("  Open http://localhost:5000 in your browser")
    print("=" * 50)
    print()
    app.run(host="0.0.0.0", port=5000, debug=False, threaded=True)
