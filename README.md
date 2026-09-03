# Clearstone Inspect

AI-assisted construction inspection documentation. An inspector records a walkthrough on their phone, drops the file in, and a structured PDF report comes back — transcript, annotated evidence frames, per-finding status, and an AI summary.

Built as a companion tool to the Clearstone construction management platform.

---

## How it works

Processing is **deferred, not live**. Nothing runs during the walkthrough. The phone is a dumb recorder; all analysis happens afterward.

```
Inspector records walkthrough on phone (native camera app)
        ↓
Drops file into uploads/ or uploads via the dashboard
        ↓
  transcribe   Whisper transcribes full audio with timestamps
  markers      Finds "Petra" flags + violation keywords in transcript
  extract      Frames every 5s, plus ±3s around every marker
  detect       YOLO batch detection, second high-res pass on markers
  correlate    Matches spoken observations to visual evidence
  report       PDF with findings, frames, transcript, AI summary
        ↓
Report ready in ~10–30 minutes
```

**The key design decision:** the inspector's voice is the primary signal, not the vision model. When they say "exposed wiring on the east wall," that's a human expert identifying a violation. YOLO's job is only to corroborate that something wire-like is in frame. This sidesteps the training-data problem — the model doesn't need to recognize violations, it needs to confirm them.

### Finding types

| Type | Meaning | Status |
|---|---|---|
| **Confirmed** | Inspector stated it, detections corroborate | FAIL if it matches `violations.json`, else CAUTION |
| **Unconfirmed** | Inspector stated it, no relevant detections | CAUTION — "stated, not visually confirmed" |
| **Unmentioned** | Detection >60% with no marker within 10s | CAUTION — "detected, not mentioned" |

That last category is the one that earns its keep: it catches what the inspector walked past.

---

## Setup

```bash
cd clearstone_inspect
pip install -r requirements.txt
```

**Requires `ffmpeg` on PATH** — used for audio extraction. Not a pip package.

Model weights (`*.pt`) download automatically on first run and are gitignored. Do not commit them.

**For the AI summary section**, set your API key before launching:

```powershell
$env:ANTHROPIC_API_KEY="your-key-here"
```

If unset, reports generate normally without the summary.

---

## Running it

**Dashboard (primary):**

```bash
python app.py
```

Open `http://localhost:5000`. Three views — Upload, Jobs, Reports.

**CLI (per-job, for debugging):**

```bash
python pipeline.py <job_id> "Project Name" "Address" "Inspector Name"
```

**Individual stages** — each is runnable alone against an existing job, in order:

```bash
python transcribe.py <job_id>
python markers.py <job_id>
python extract.py <job_id>
python detect.py <job_id>
```

**Watch the uploads folder once:**

```bash
python -c "from ingest import scan_uploads_once; print(scan_uploads_once())"
```

---

## File map

| File | Does |
|---|---|
| `app.py` | Flask server, all HTTP routes, job endpoints |
| `pipeline.py` | Orchestrates the full stage sequence for one job |
| `jobs.py` | Background job queue, state machine, progress persistence |
| `ingest.py` | Watches `uploads/`, creates job directories |
| `transcribe.py` | ffmpeg audio extraction → Whisper with segment timestamps |
| `markers.py` | Identifies moments of interest in the transcript |
| `extract.py` | Pulls frames — baseline sweep plus marker windows |
| `detect.py` | Batch YOLO over extracted frames, writes annotated copies |
| `correlate.py` | Matches transcript to frames, produces findings |
| `report.py` | PDF generation, Claude AI summary |
| `logger.py` | Log persistence |
| `utils.py` | Shared helpers — paths, timestamps, filenames |
| `violations.json` | Violation knowledge base |
| `templates/`, `static/` | Dashboard UI |

**Job artifacts** live in `jobs/<job_id>/` — `source.mp4`, `transcript.json`, `frames/`, `annotated/`, `findings.json`, `status.json`, and the generated PDF.

---

## Design system

Shared with the Clearstone platform.

```
Background   #F0EDE8   warm stone
Primary      #1B3F5E   navy
Secondary    #2D6A52   green — pass
Attention    #D97706   amber — caution
                       red   — fail
Display font Fraunces (serif)
Cards        rounded-lg, left border accent
```

---

## Known limitations

- **Manual file transfer.** The inspector moves the video off their phone by hand. Accepted tradeoff for v1 — it eliminated the entire class of live-camera bugs (Camo, camera indices, device locks) that dominated early development.
- **One violation in the knowledge base.** `violations.json` has a single entry (Exposed Electrical Wiring). The per-violation matching logic has not been stress-tested with multiple entries.
- **Detection is corroborative, not diagnostic.** YOLO recognizes general objects, not code violations. Findings originate from the transcript.
- **Upload size.** Flask's `MAX_CONTENT_LENGTH` needs to be raised or unset for long recordings — a 20-minute 4K video can exceed 5GB.
- **Processing time scales with recording length.** ~10–30 min is the target for a typical walkthrough. If it overruns, the first knob is the model in `detect.py` (`yolov8m-oiv7` → `yolov8n-oiv7`) and the second is the 5-second baseline interval in `extract.py`.

---

## Git notes

Model weights and runtime output are gitignored. If a push is rejected for file size, the cause is a `.pt` file in an earlier commit — `git rm --cached <file>` only fixes it going forward; stripping it from history needs `git filter-repo`.

Pre-restructure history is preserved on the `old-history` branch.

---

## Roadmap

- Real-site validation — record actual walkthroughs, verify all three finding types fire correctly
- Expand `violations.json` beyond one entry, stress-test multi-violation matching
- Claude vision on extracted frames as an optional analysis path — would largely replace the need for violation-specific training data
- Direct integration with the Clearstone platform: reports post to the GC dashboard and homeowner portal automatically
