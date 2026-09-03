# Clearstone Inspect — Handoff Document

**Status:** Parked deliberately, not abandoned
**Date parked:** September 2026
**Reason:** No site access for validation. Focus shifted to the Clearstone platform and the Umbra Companies opportunity.
**Repo:** `clearstone_inspect` — local path `C:\Users\tyosh\Documents\python glasses\clearstone_inspect`

---

## What this is

An AI-assisted construction inspection documentation tool. A superintendent or inspector records a walkthrough on their phone, drops the file in, and gets back a structured PDF — transcript, annotated evidence frames, per-finding status, AI summary.

Built with no prior Python experience, from scratch, over roughly three months.

---

## Architecture — deferred, not live

The project went through a full restructure. It **used to** run live real-time YOLO detection on a streaming camera feed. That was the wrong design and it was removed.

Current pipeline:

```
Inspector records walkthrough on phone (native camera app)
        ↓
Drops file into uploads/ or uploads via dashboard
        ↓
  transcribe   Whisper, full audio, segment timestamps
  markers      "Petra" flags + violation keyword vocabulary
  extract      Frames every 5s + ±3s around every marker
  detect       YOLO batch, second high-res pass on markers
  correlate    Matches spoken observations to visual evidence
  report       PDF: findings, frames, transcript, AI summary
        ↓
Report in ~10-30 minutes
```

**The core design decision:** the inspector's voice is the primary signal, not the vision model. When they say "exposed wiring on the east wall," that's a human expert identifying a violation. YOLO only corroborates that something relevant is in frame. This sidesteps the training-data problem entirely — the model doesn't need to recognize violations, only confirm them.

### Finding types

| Type | Meaning | Status |
|---|---|---|
| Confirmed | Stated by inspector, detections corroborate | FAIL if matches `violations.json`, else CAUTION |
| Unconfirmed | Stated, no relevant detections | CAUTION — "stated, not visually confirmed" |
| Unmentioned | Detection >60%, no marker within 10s | CAUTION — "detected, not mentioned" |

Unmentioned is the differentiated one — it catches what the inspector walked past.

---

## Build phases completed

**Phase 1 — Demolition.** Deleted `main.py`, `README.md`, and all of `capture.py` except `generate_filename()` and `extract_video_thumbnail()` (moved to `utils.py`). Removed `CameraStream`, `/video_feed`, MJPEG streaming, live overlay, camera index UI, all polling loops from the live era, dead code identified in the inventory.

**Phase 2 — Pipeline.** Built `ingest.py`, `transcribe.py`, `markers.py`, `extract.py`, `detect.py`, `correlate.py`, `jobs.py`, `pipeline.py`. Updated `logger.py` and rewrote `report.py`. Every module unit-tested independently.

**Phase 3 — Dashboard rewire.** Upload / Jobs / Findings / Reports views replacing the live camera dashboard. **Status uncertain — verify this actually ran before doing anything else.**

**Post-Phase-3 fixes prompt written but not confirmed run:**
1. Zero-finding reports (a clean inspection should still produce a PASS document)
2. Expanded keyword vocabulary + negation handling ("the wiring looks good" should not flag)
3. Relaxed `CONSTRUCTION_RELEVANT_CLASSES` from 11 items to ~35

---

## First thing to do on return

Verify what actually exists. In Claude Code:

```
Do not change any code. Tell me whether app.py has these routes:
/api/upload, /api/jobs, /api/jobs/<job_id>. Tell me whether
templates/index.html has an Upload view with a drop zone.
Tell me whether report.py generates a report when findings is
empty. Just answer yes or no for each.
```

If Phase 3 didn't run, the Phase 3 prompt and the three-fix prompt are both in the chat history that produced this document.

---

## The unresolved problem

**Nothing has ever been tested on a real construction walkthrough.**

Every test used synthetic data — a sine-tone video with no speech, fabricated markers, photos pulled from `output/`. The one real recording was a talk-to-camera video, which correctly produced zero findings (YOLO saw `Glasses`, `Human face`, `Man` — none construction-relevant).

The correlator works against data built to match it. Whether it works against a real site is unknown.

**This is why the project is parked.** No site access means no validation. The moment there's a customer with active projects, that's the test environment.

### The test to run when a site is available

Record 5–10 minutes deliberately exercising all four paths:

- "Petra, exposed wiring here" while pointing at a cable or panel → **CONFIRMED**
- "Petra, the drywall is cracked" pointing at nothing detectable → **UNCONFIRMED**
- Leave a ladder or extension cord in frame, say nothing → **UNMENTIONED**
- Talk about something unrelated for 30 seconds → **nothing**

---

## Technical notes worth remembering

**The wake word is optional and should stay that way.** "Petra" was demoted from gate to emphasis marker in Phase 2. The `keyword` marker path fires with no wake word at all. Petra's only remaining value is disambiguating intent — "the exposed wiring got fixed last week" vs "exposed wiring, right here" trip identical keywords. Keep it as a power-user shortcut. Never require it, never explain it in a demo.

**A bug that was fixed by the restructure, worth not reintroducing.** The old `analyze_image()` applied `max(detection_confidences)` uniformly across every entry in `violations.json` rather than matching per-object. With one violation in the KB this was invisible — every log entry read "Exposed Electrical Wiring, 96%" regardless of what YOLO actually saw. `correlate.py` derives the violation name from the transcript instead. Don't carry the old logic back in.

**`violations.json` has exactly one entry.** The multi-violation matching path has never been exercised. Stress-test with a second entry before assuming it scales.

**YOLO is not carrying this product.** OIV7's construction-relevant classes are sparse and unreliable in practice. The transcript is doing the work. The highest-leverage improvement is the keyword vocabulary in `markers.py`, not the model.

**Model weights are gitignored.** `*.pt` files download automatically on first run. A 131MB `yolov8x-oiv7.pt` in an early commit is what forced the git history rewrite. Current model chain: `yolov8m-oiv7.pt` → `yolov8n-oiv7.pt` fallback.

**`voice.py` may now be dead.** `transcribe.py` replaced wake-word-gated capture with full transcription. Check whether anything still imports it. If not, delete it and untrack the Vosk model (24MB, still in the repo).

**Requires `ffmpeg` on PATH** — not a pip package. Already installed via Gyan.FFmpeg.

**`ANTHROPIC_API_KEY`** env var gates the AI summary section. Reports generate without it.

**Flask `MAX_CONTENT_LENGTH`** needs raising for long recordings. A 20-minute 4K phone video can exceed 5GB.

---

## Git state

History was rewritten via orphan branch + force push to strip oversized model weights. Current `main` is a single commit, 34 files, clean.

**Outstanding git tasks:**

```bash
# Recover pre-restructure history (commit c97a4a1 was the old tip)
git branch old-history c97a4a1
git log old-history --oneline

# Untrack the Vosk model
git rm -r --cached vosk-model-small-en-us-0.15/
echo "vosk-model-small-en-us-0.15/" >> .gitignore
git commit -m "Untrack Vosk model weights"
```

`old-history` will fail to push (the 131MB file is in that history). Keep it local and also copy the project folder somewhere as a plain archive — a local-only branch is a weak backup. `git filter-repo` would strip it properly if the public history matters later.

---

## Strategic context

**Who the customer is — and isn't.** The word "inspector" collapses four groups with opposite economics:

| Who | Buys software? |
|---|---|
| Municipal / AHJ code inspector | **No** — city procurement, 12–24 month cycles, no incentive |
| GC superintendent doing pre-inspection walks | **Yes** — this is the buyer |
| Third-party special inspector (ICC-certified) | Yes |
| Home / warranty inspector | Yes, but already well served |

The AHJ is not a customer. They inspect against a checklist, mark pass/fail in the city system, and leave. Nothing you hand them changes the outcome.

**The real positioning:**

> Most failed inspections aren't bad work — they're missing proof. Clearstone Inspect turns the walk you already do before the inspector arrives into timestamped, photo-backed documentation, automatically.

**Stop saying "for inspectors."** It points at a buyer who can't buy.

**A caution on the "73% of violations are documentation gaps" stat.** It came from a CMMS vendor blog about commercial facility management, with no primary source. Don't put the number in a deck. The directional claim — failed inspections are often about missing records rather than bad work — is well supported qualitatively across multiple sources. Say it that way, or find a primary source first.

---

## How this fits with Clearstone

Inspect is a **roadmap slide, not a product slide.** "AI-assisted site documentation, in development" signals technical depth without promising a demo.

The honest founder framing: it was built, it works technically, and it's held until there are real sites to validate against. That reads better than juggling two half-products.

**Natural integration point when it resumes:** reports post directly to the GC dashboard and homeowner portal. That integration — inspection feeding project management feeding homeowner transparency — is the actual moat. Standalone inspection tools already exist (InspectFast, AECify, Field1st, Hardline). The full loop doesn't.

---

## Roadmap when it resumes

1. Verify Phase 3 shipped; run the three post-Phase-3 fixes if not
2. Real-site validation — all four paths, actual walkthrough
3. Expand `violations.json` past one entry, stress-test multi-violation matching
4. Expand the `markers.py` keyword vocabulary — highest leverage, lowest cost
5. Evaluate Claude vision on extracted frames as an optional analysis path. Would largely eliminate the need for violation-specific training data. Roughly $0.15–0.50 per inspection against a $200–400/mo subscription. The "must run offline" constraint was an assumption, never a customer requirement — worth re-testing before architecting around it.
6. Clearstone platform integration
