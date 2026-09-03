import json
import os
import queue
import time

import numpy as np
import sounddevice as sd
import whisper
from vosk import KaldiRecognizer, Model


SAMPLE_RATE = 16000
CHANNELS = 1
BLOCK_DURATION = 0.2  # seconds per audio callback block
BLOCK_SIZE = int(SAMPLE_RATE * BLOCK_DURATION)

WAKE_WORD = "petra"
STOP_PHRASE = "petra done"

PRE_ROLL_SECONDS = 3
SILENCE_SECONDS = 2.5
SILENCE_THRESHOLD = 300  # RMS threshold (int16 scale) below which audio counts as silence

MODEL_DIR = os.path.join(os.path.dirname(__file__), "vosk-model-small-en-us-0.15")

# Preferred real microphone name (substring match, case-insensitive).
# Hardcoding a device *index* is fragile — connecting/disconnecting any
# audio device (e.g. a Bluetooth headset) shifts every index below it,
# silently pointing this at the wrong microphone. Matching by name is
# stable across those changes.
PREFERRED_INPUT_NAME = "microphone array"

_whisper_model = None


def get_default_input_device():
    """
    Picks a real microphone by name instead of a hardcoded index, so
    connecting/disconnecting other audio devices (e.g. a Bluetooth
    headset) can't silently redirect voice capture to the wrong device.

    Prefers a device whose name contains PREFERRED_INPUT_NAME (the
    laptop's built-in mic array); falls back to sounddevice's own
    default input device if no match is found.
    """
    try:
        devices = sd.query_devices()
    except Exception:
        return None

    for i, d in enumerate(devices):
        if d.get("max_input_channels", 0) > 0 and PREFERRED_INPUT_NAME in d.get("name", "").lower():
            return i

    try:
        default_index = sd.default.device[0]
        if default_index is not None and default_index >= 0:
            return default_index
    except Exception:
        pass

    return None


def get_whisper_model(size="base"):
    global _whisper_model
    if _whisper_model is None:
        print(f"Loading Whisper model ({size})...")
        _whisper_model = whisper.load_model(size)
    return _whisper_model


class RollingBuffer:
    """Fixed-length ring buffer holding the last N seconds of int16 audio."""

    def __init__(self, seconds, sample_rate=SAMPLE_RATE):
        self.max_samples = int(seconds * sample_rate)
        self.buffer = np.zeros(self.max_samples, dtype=np.int16)
        self.filled = 0

    def push(self, chunk):
        chunk = chunk.flatten()
        n = len(chunk)
        if n >= self.max_samples:
            self.buffer[:] = chunk[-self.max_samples:]
        else:
            self.buffer = np.roll(self.buffer, -n)
            self.buffer[-n:] = chunk
        self.filled = min(self.filled + n, self.max_samples)

    def snapshot(self):
        return self.buffer[-self.filled:].copy() if self.filled < self.max_samples else self.buffer.copy()


def rms(chunk):
    if len(chunk) == 0:
        return 0.0
    return float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))


def listen_for_notes(
    on_note_captured,
    whisper_model_size="base",
    device=None,
    debug=False,
    on_wake_detected=None,
    on_transcribing_start=None,
):
    """
    Continuously listens on the microphone for the wake word "Petra".

    Blocks forever (run this in a background thread from main.py).

    on_note_captured: callback invoked with the transcribed note text (str)
                       as soon as a note has been captured and transcribed.
                       This is where main.py should trigger a photo + YOLO
                       analysis.

    on_wake_detected: optional callback invoked (no arguments) the instant
                       the wake word is detected and recording begins.
                       Lets a caller (e.g. the web dashboard) surface a
                       "Listening..." state to the user in real time.

    on_transcribing_start: optional callback invoked (no arguments) right
                       before Whisper starts transcribing the captured
                       audio. Lets a caller surface a "Processing..."
                       state while transcription is in progress.

    debug: when True, prints live mic level / partial recognition text.
           Off by default since it interleaves badly with main.py's menu
           prompts when running in a background thread; main.py's status
           line handles a lightweight version of this instead.
    """
    if not os.path.isdir(MODEL_DIR):
        raise FileNotFoundError(
            f"Vosk model not found at {MODEL_DIR}. "
            "Download vosk-model-small-en-us-0.15 and unzip it there."
        )

    if device is None:
        device = get_default_input_device()
        if device is None:
            raise RuntimeError(
                "No usable microphone found. Check that a microphone is "
                "connected and Windows has granted microphone permission."
            )

    print("Loading Vosk model...")
    vosk_model = Model(MODEL_DIR)
    get_whisper_model(whisper_model_size)

    pre_roll = RollingBuffer(PRE_ROLL_SECONDS)
    audio_q = queue.Queue()

    def audio_callback(indata, frames, time_info, status):
        if status:
            print(f"Audio status: {status}")
        audio_q.put(indata.copy())

    print(f'Listening for wake word "{WAKE_WORD.title()}"...')

    if device is not None:
        print(f"Using input device: {sd.query_devices(device)['name']}")
    else:
        default_in = sd.query_devices(sd.default.device[0])
        print(f"Using default input device: {default_in['name']}")

    with sd.InputStream(
        samplerate=SAMPLE_RATE,
        channels=CHANNELS,
        dtype="int16",
        blocksize=BLOCK_SIZE,
        callback=audio_callback,
        device=device,
    ):
        state = "waiting"  # "waiting" -> "recording"
        recognizer = KaldiRecognizer(vosk_model, SAMPLE_RATE)
        recorded_chunks = []
        silence_run = 0.0
        recent_words = ""

        while True:
            chunk = audio_q.get()
            mono = chunk[:, 0] if chunk.ndim > 1 else chunk

            if state == "waiting":
                pre_roll.push(mono)

                if debug:
                    level = rms(mono)
                    bar = "#" * int(min(level, 3000) / 60)
                    print(f"\rmic level: {level:7.1f} {bar:<50}", end="", flush=True)

                data_bytes = mono.tobytes()
                heard_wake = False
                if recognizer.AcceptWaveform(data_bytes):
                    text = json.loads(recognizer.Result()).get("text", "")
                    if text and debug:
                        print(f"\n[heard]: {text}")
                    if WAKE_WORD in text.lower():
                        heard_wake = True
                else:
                    partial = json.loads(recognizer.PartialResult()).get("partial", "")
                    if partial and debug:
                        print(f"\r[partial]: {partial:<60}", end="", flush=True)
                    if WAKE_WORD in partial.lower():
                        heard_wake = True

                if heard_wake:
                    print("\n  [voice] Wake word detected — recording note...")
                    if on_wake_detected:
                        try:
                            on_wake_detected()
                        except Exception as exc:
                            print(f"  [voice] on_wake_detected callback error: {exc}")
                    recognizer.Reset()
                    recorded_chunks = [pre_roll.snapshot()]
                    silence_run = 0.0
                    recent_words = ""
                    state = "recording"

            elif state == "recording":
                recorded_chunks.append(mono)

                level = rms(mono)
                if level < SILENCE_THRESHOLD:
                    silence_run += BLOCK_DURATION
                else:
                    silence_run = 0.0

                # Check the stop phrase against a short rolling window of
                # recognized words (last finalized segment + current
                # partial), not just one segment in isolation — otherwise
                # "petra" landing at the end of one segment and "done" at
                # the start of the next would never match together.
                data_bytes = mono.tobytes()
                if recognizer.AcceptWaveform(data_bytes):
                    text = json.loads(recognizer.Result()).get("text", "")
                    if text:
                        recent_words = (recent_words + " " + text).strip()
                    partial = ""
                else:
                    partial = json.loads(recognizer.PartialResult()).get("partial", "")

                window = f"{recent_words} {partial}".strip().lower()
                # Keep the window from growing unbounded — only the tail
                # matters for matching the stop phrase.
                recent_words = " ".join(recent_words.split()[-10:])

                said_done = STOP_PHRASE in window

                if said_done or silence_run >= SILENCE_SECONDS:
                    print("  [voice] End of note detected — transcribing...")
                    if on_transcribing_start:
                        try:
                            on_transcribing_start()
                        except Exception as exc:
                            print(f"  [voice] on_transcribing_start callback error: {exc}")
                    full_audio = np.concatenate(recorded_chunks)
                    note_text = _transcribe(full_audio)
                    print(f'  [voice] Note captured: "{note_text}"')

                    if note_text.strip():
                        on_note_captured(note_text.strip())

                    # Reset for next wake word
                    recognizer = KaldiRecognizer(vosk_model, SAMPLE_RATE)
                    recorded_chunks = []
                    silence_run = 0.0
                    state = "waiting"
                    print(f'  [voice] Listening for wake word "{WAKE_WORD.title()}"...\n')


def _transcribe(int16_audio):
    audio_float = int16_audio.astype(np.float32) / 32768.0
    model = get_whisper_model()
    result = model.transcribe(audio_float, fp16=False)
    return result.get("text", "")
