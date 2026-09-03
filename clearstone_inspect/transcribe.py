"""
Extracts the audio track from a job's source video and runs Whisper on
the full audio with segment-level timestamps, replacing the old
wake-word-gated voice.py pipeline entirely — every word the inspector
says during the walkthrough gets transcribed, not just what followed
"Petra".
"""

import json
import os
import subprocess
import shutil

import whisper

from utils import ensure_dir

JOBS_DIR = "jobs"

_whisper_model = None


def get_whisper_model(size="base"):
    global _whisper_model
    if _whisper_model is None:
        print(f"[transcribe] Loading Whisper model ({size})...")
        _whisper_model = whisper.load_model(size)
    return _whisper_model


def _ffmpeg_path():
    return shutil.which("ffmpeg")


def extract_audio(video_path, audio_path):
    """
    Extracts the audio track from video_path into audio_path (16kHz mono
    WAV, what Whisper wants) using ffmpeg if it's on PATH. Falls back to
    reading the video via OpenCV's own audio-less frame reader is not
    possible for audio — OpenCV has no audio API — so if ffmpeg isn't
    available, this raises rather than silently producing no audio.

    Returns audio_path on success.
    """
    ffmpeg = _ffmpeg_path()
    if not ffmpeg:
        raise RuntimeError(
            "ffmpeg not found on PATH. Audio extraction requires ffmpeg "
            "(OpenCV has no audio API) — install it and make sure "
            "'ffmpeg' is runnable from the command line."
        )

    cmd = [
        ffmpeg,
        "-y",                # overwrite output without prompting
        "-i", video_path,
        "-vn",               # no video
        "-ac", "1",          # mono
        "-ar", "16000",      # 16kHz, what Whisper expects
        "-f", "wav",
        audio_path,
    ]

    result = subprocess.run(
        cmd, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg failed extracting audio from {video_path}: "
            f"{result.stderr.strip()[-500:]}"
        )

    if not os.path.exists(audio_path):
        raise RuntimeError(
            f"ffmpeg reported success but {audio_path} was not created."
        )

    return audio_path


def transcribe_job(job_id, whisper_model_size="base"):
    """
    Finds jobs/<job_id>/source.<ext>, extracts its audio, runs Whisper
    with segment-level timestamps, and saves the full transcript to
    jobs/<job_id>/transcript.json.

    Returns the list of segments: [{start, end, text}, ...].
    """
    job_dir = os.path.join(JOBS_DIR, job_id)
    if not os.path.isdir(job_dir):
        raise FileNotFoundError(f"No such job directory: {job_dir}")

    source_path = None
    for filename in os.listdir(job_dir):
        if filename.startswith("source."):
            source_path = os.path.join(job_dir, filename)
            break

    if source_path is None:
        raise FileNotFoundError(f"No source video found in {job_dir}")

    audio_path = os.path.join(job_dir, "audio.wav")
    print(f"[transcribe] Job {job_id}: extracting audio from {source_path}...")
    extract_audio(source_path, audio_path)

    print(f"[transcribe] Job {job_id}: running Whisper on {audio_path}...")
    model = get_whisper_model(whisper_model_size)
    result = model.transcribe(audio_path, word_timestamps=True, verbose=False)

    segments = []
    for seg in result.get("segments", []):
        segments.append({
            "start": round(float(seg["start"]), 2),
            "end": round(float(seg["end"]), 2),
            "text": seg["text"].strip(),
        })

    transcript = {
        "job_id": job_id,
        "full_text": result.get("text", "").strip(),
        "segments": segments,
    }

    transcript_path = os.path.join(job_dir, "transcript.json")
    with open(transcript_path, "w") as f:
        json.dump(transcript, f, indent=2)

    print(f"[transcribe] Job {job_id}: {len(segments)} segment(s) saved to "
          f"{transcript_path}")

    return segments


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python transcribe.py <job_id>")
        sys.exit(1)

    transcribe_job(sys.argv[1])
