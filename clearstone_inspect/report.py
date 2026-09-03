import json
import os
from datetime import datetime
from fpdf import FPDF

try:
    import anthropic
except ImportError:
    anthropic = None

from utils import seconds_to_mmss, ensure_dir


MARGIN_MM = 15
PAGE_WIDTH_MM = 210  # A4 width; content area is PAGE_WIDTH_MM - 2*MARGIN_MM
CONTENT_WIDTH_MM = PAGE_WIDTH_MM - (2 * MARGIN_MM)
MAX_PHOTO_WIDTH_MM = 80

FONT_TITLE = 24
FONT_SECTION = 14
FONT_BODY = 10
FONT_CAPTION = 8

STONE_BG = (240, 237, 232)

STATUS_COLORS = {
    "FAIL": (185, 28, 28),      # red
    "CAUTION": (217, 119, 6),   # amber
    "PASS": (45, 106, 82),      # green
}

CLAUDE_MODEL = "claude-sonnet-5"


class ReportPDF(FPDF):
    """FPDF subclass that adds the required footer to every page:
    left = 'Clearstone Inspect', center = 'Page X of Y', right = the
    date the report was generated.

    Also prints a "(continued)" label at the top of a new page, but
    only when fpdf2's own automatic page break fires while a finding
    card is mid-render (watching_for_break=True) — i.e. an actual split,
    not just a card that happens to start on a fresh page."""

    generated_date_str = ""
    watching_for_break = False

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", FONT_CAPTION)
        self.set_text_color(120, 120, 120)

        page_label = f"Page {self.page_no()} of {{nb}}"

        self.cell(CONTENT_WIDTH_MM / 3, 8, "Clearstone Inspect", align="L")
        self.cell(CONTENT_WIDTH_MM / 3, 8, page_label, align="C")
        self.cell(CONTENT_WIDTH_MM / 3, 8, self.generated_date_str, align="R")
        self.set_text_color(0, 0, 0)

    def header(self):
        if self.watching_for_break and self.page_no() > 1:
            self.set_font("Helvetica", "I", FONT_CAPTION)
            self.set_text_color(120, 120, 120)
            self.cell(0, 5, "(continued)", new_x="LMARGIN", new_y="NEXT")
            self.set_text_color(0, 0, 0)
            self.ln(1)


def load_findings(job_id):
    """Loads jobs/<job_id>/findings.json, written by correlate.py."""
    findings_path = os.path.join("jobs", job_id, "findings.json")
    with open(findings_path, "r") as f:
        data = json.load(f)
    return data.get("findings", [])


def load_transcript(job_id):
    """Loads jobs/<job_id>/transcript.json, written by transcribe.py.
    Returns the segments list, or an empty list if no transcript exists
    (e.g. a silent walkthrough, or transcription was skipped)."""
    transcript_path = os.path.join("jobs", job_id, "transcript.json")
    if not os.path.exists(transcript_path):
        return []
    with open(transcript_path, "r") as f:
        data = json.load(f)
    return data.get("segments", [])


def get_overall_status(findings):
    statuses = [f.get("status", "PASS") for f in findings]
    if "FAIL" in statuses:
        return "FAIL"
    if "CAUTION" in statuses:
        return "CAUTION"
    return "PASS"


def _build_ai_summary_input(findings, transcript_segments):
    """
    Turns the findings and transcript into a compact plain-text summary
    suitable for sending to Claude — full detail, but without embedding
    binary data (photos) or internal file paths.

    When findings is empty, this deliberately says so explicitly rather
    than leaving a blank "Findings:" section — Claude is told the
    walkthrough was clean so it can write a summary that reflects the
    transcript alone, describing what was observed instead of inventing
    or implying violations that were never found.
    """
    if not findings:
        lines = [
            "No violations were identified during this walkthrough. "
            "Base the summary entirely on what the inspector describes "
            "in the transcript below — describe the site condition and "
            "what was observed, not on any violation."
        ]
        if transcript_segments:
            lines.append("")
            lines.append("Full walkthrough transcript:")
            for seg in transcript_segments:
                lines.append(f"[{seconds_to_mmss(seg['start'])}] {seg['text']}")
        return "\n".join(lines)

    lines = ["Findings:"]
    for i, finding in enumerate(findings, 1):
        status = finding.get("status", "PASS")
        finding_type = finding.get("finding_type", "N/A")
        name = finding.get("violation_name", "N/A")
        timestamp = finding.get("timestamp", 0)
        trade = finding.get("trade_responsible") or "N/A"
        excerpt = finding.get("transcript_excerpt")
        label = finding.get("label")
        detections = finding.get("detections") or []
        classes = ", ".join(sorted({d["class"] for d in detections})) or "none"

        line = (
            f"{i}. [{status}/{finding_type}] {name} at {seconds_to_mmss(timestamp)} "
            f"— trade: {trade}, detected objects: {classes}"
        )
        if label:
            line += f" ({label})"
        if excerpt:
            line += f", inspector said: \"{excerpt}\""
        lines.append(line)

    if transcript_segments:
        lines.append("")
        lines.append("Full walkthrough transcript:")
        for seg in transcript_segments:
            lines.append(f"[{seconds_to_mmss(seg['start'])}] {seg['text']}")

    return "\n".join(lines)


def generate_ai_summary(findings, transcript_segments):
    """
    Sends the findings and full transcript to the Claude API and returns
    a short TLDR summary paragraph (or None if the API isn't
    available/configured, or the call fails for any reason — the report
    should still generate without this section rather than crash).
    """
    if anthropic is None:
        print("AI summary skipped: 'anthropic' package is not installed.")
        return None

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("AI summary skipped: ANTHROPIC_API_KEY environment variable is not set.")
        return None

    system_prompt = (
        "You are a professional construction inspection assistant. Based "
        "on the findings and full walkthrough transcript provided, write "
        "a concise professional TLDR summary of 2 to 3 paragraphs. "
        "Include: overall site condition assessment, the most critical "
        "issues found, which trades need to be called, and a recommended "
        "priority order for addressing violations. Write in plain English "
        "that a homeowner or GC can understand. Be direct and specific. "
        "Do not use bullet points."
    )

    summary_input = _build_ai_summary_input(findings, transcript_segments)

    try:
        client = anthropic.Anthropic(api_key=api_key)
        response = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=600,
            system=system_prompt,
            messages=[
                {"role": "user", "content": summary_input}
            ],
        )
        text_parts = [block.text for block in response.content if hasattr(block, "text")]
        summary = "\n".join(text_parts).strip()
        summary = _sanitize_for_pdf(summary)
        return summary or None
    except Exception as exc:
        print(f"AI summary skipped: Claude API call failed ({exc}).")
        return None


# Helvetica (a core PDF font) only supports latin-1 — Claude's prose
# commonly uses characters outside that range (em/en dashes, curly
# quotes, ellipsis). Rather than risk generate_report() crashing on
# whatever Claude happens to write, replace the common offenders with
# safe latin-1 equivalents before the text ever reaches FPDF.
_PDF_SAFE_REPLACEMENTS = {
    "—": "-",   # em dash
    "–": "-",   # en dash
    "‘": "'",   # left single quote
    "’": "'",   # right single quote
    "“": '"',   # left double quote
    "”": '"',   # right double quote
    "…": "...", # ellipsis
    "•": "-",   # bullet (shouldn't appear given the prompt, but just in case)
}


def _sanitize_for_pdf(text):
    for bad, good in _PDF_SAFE_REPLACEMENTS.items():
        text = text.replace(bad, good)
    # Anything else outside latin-1 gets dropped rather than crashing
    # the whole report.
    return text.encode("latin-1", errors="ignore").decode("latin-1")


def _estimate_card_height(finding, has_photo):
    """
    Rough height (mm) a finding card will take, used to decide whether
    it fits on the remaining space of the current page. Doesn't need to
    be exact — just close enough to avoid an obviously-avoidable split.
    """
    height = 8       # title row
    height += 8      # badge row
    height += 6 * 3  # trade / timestamp / label rows
    excerpt = finding.get("transcript_excerpt")
    if excerpt:
        lines = max(1, (len(excerpt) // 45) + 1)
        height += 5 + (lines * 5)
    if has_photo:
        height += 2 + (MAX_PHOTO_WIDTH_MM * 0.65)  # rough photo aspect guess
        height += 2  # spacing after photo
        detections = finding.get("detections") or []
        height += 5 + (5 * max(1, len(detections)))  # detected-objects box lines
    height += 8  # trailing margin
    return height


def _render_finding_card(pdf, index, finding):
    """
    Renders one finding as a clean bordered card. If the card doesn't
    fit in the remaining space on the current page, it starts a fresh
    page for it (normal pagination — no "continued" label, since nothing
    was actually split). If the card's content genuinely doesn't fit
    even on a fresh page, fpdf2's own auto page break takes over
    mid-card, and only then does the page's header print a
    "(continued)" label (see ReportPDF.header / watching_for_break).
    """
    name = finding.get("violation_name", "N/A")
    status = finding.get("status", "PASS")
    finding_type = finding.get("finding_type", "N/A")
    label = finding.get("label")
    trade = finding.get("trade_responsible") or "N/A"
    timestamp = finding.get("timestamp", 0)
    excerpt = finding.get("transcript_excerpt")
    frame_path = finding.get("annotated_frame_path", "")
    detections = finding.get("detections") or []
    status_color = STATUS_COLORS.get(status, (0, 0, 0))
    has_photo = bool(frame_path and os.path.exists(frame_path))

    estimated_height = _estimate_card_height(finding, has_photo)
    remaining_space = pdf.h - pdf.b_margin - pdf.get_y()

    if estimated_height > remaining_space:
        # This card won't fit in what's left of the current page — start
        # it fresh on a new page. This is normal pagination, not a split,
        # so no "(continued)" label here.
        pdf.add_page()

    pdf.watching_for_break = True
    card_start_page = pdf.page_no()
    card_top = pdf.get_y()

    pdf.set_font("Helvetica", "B", FONT_BODY + 1)
    pdf.cell(0, 7, f"Finding #{index}: {name}", new_x="LMARGIN", new_y="NEXT")

    # Color-coded status badge, plus the finding type (confirmed /
    # unconfirmed / unmentioned) right after it.
    pdf.set_fill_color(*status_color)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", FONT_CAPTION + 1)
    badge_width = pdf.get_string_width(status) + 8
    pdf.cell(badge_width, 6, status, align="C", fill=True)
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "I", FONT_CAPTION)
    pdf.cell(0, 6, f"  {finding_type}", new_x="LMARGIN", new_y="NEXT")

    pdf.set_font("Helvetica", "", FONT_BODY)
    pdf.cell(35, 6, "  Timestamp:", new_x="RIGHT")
    pdf.cell(0, 6, seconds_to_mmss(timestamp), new_x="LMARGIN", new_y="NEXT")
    pdf.cell(35, 6, "  Trade:", new_x="RIGHT")
    pdf.cell(0, 6, trade, new_x="LMARGIN", new_y="NEXT")

    if label:
        pdf.cell(35, 6, "  Note:", new_x="RIGHT")
        pdf.cell(0, 6, label, new_x="LMARGIN", new_y="NEXT")

    if excerpt:
        pdf.set_font("Helvetica", "I", FONT_BODY)
        pdf.set_x(pdf.l_margin + 6)
        pdf.multi_cell(CONTENT_WIDTH_MM - 12, 6, f'"{excerpt}"')
        pdf.set_font("Helvetica", "", FONT_BODY)

    if has_photo:
        pdf.ln(2)
        try:
            photo_x = pdf.l_margin + (CONTENT_WIDTH_MM - MAX_PHOTO_WIDTH_MM) / 2
            pdf.image(frame_path, x=photo_x, w=MAX_PHOTO_WIDTH_MM)
        except Exception:
            pdf.set_font("Helvetica", "", FONT_BODY)
            pdf.cell(0, 6, "  [Photo could not be loaded]", new_x="LMARGIN", new_y="NEXT")

        pdf.ln(2)
        box_top = pdf.get_y()
        pdf.set_font("Helvetica", "B", FONT_CAPTION + 1)
        pdf.cell(0, 5, "  Detected Objects", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", "", FONT_CAPTION + 1)
        if detections:
            for det in detections:
                pdf.cell(0, 5, f"    - {det['class']}: {det['confidence']}%",
                         new_x="LMARGIN", new_y="NEXT")
        else:
            pdf.cell(0, 5, "    (none)", new_x="LMARGIN", new_y="NEXT")
        box_bottom = pdf.get_y()
        pdf.rect(pdf.l_margin - 3, box_top - 1, CONTENT_WIDTH_MM + 6, box_bottom - box_top + 2)

    card_bottom = pdf.get_y()
    ended_on_same_page = pdf.page_no() == card_start_page

    pdf.watching_for_break = False

    if ended_on_same_page:
        pdf.rect(pdf.l_margin - 1, card_top - 1, CONTENT_WIDTH_MM + 2, card_bottom - card_top + 2)

    pdf.ln(6)


def _render_transcript_section(pdf, transcript_segments):
    pdf.add_page()
    pdf.set_font("Helvetica", "B", FONT_SECTION)
    pdf.cell(0, 8, "Full Walkthrough Transcript", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    if not transcript_segments:
        pdf.set_font("Helvetica", "I", FONT_BODY)
        pdf.cell(0, 6, "No transcript available for this walkthrough.",
                 new_x="LMARGIN", new_y="NEXT")
        return

    pdf.set_font("Helvetica", "", FONT_BODY)
    for seg in transcript_segments:
        timestamp_label = seconds_to_mmss(seg["start"])
        pdf.set_font("Helvetica", "B", FONT_CAPTION + 1)
        pdf.cell(18, 6, f"[{timestamp_label}]", new_x="RIGHT")
        pdf.set_font("Helvetica", "", FONT_BODY)
        pdf.multi_cell(CONTENT_WIDTH_MM - 18, 6, seg["text"])


def generate_report(job_id, project_name, address, inspector_name, notes=""):
    """
    Builds the inspection report PDF for one job from
    jobs/<job_id>/findings.json (written by correlate.py) and
    jobs/<job_id>/transcript.json (written by transcribe.py).

    A clean walkthrough (zero findings) still produces a report — a
    valid "no violations identified" result is not the same as "nothing
    to report." The report always generates; only its content changes
    when there's nothing to flag.
    """
    findings = load_findings(job_id)
    transcript_segments = load_transcript(job_id)

    now = datetime.now()
    overall_status = get_overall_status(findings)

    total = len(findings)
    statuses = [f.get("status", "PASS") for f in findings]
    fail_count = statuses.count("FAIL")
    caution_count = statuses.count("CAUTION")
    pass_count = statuses.count("PASS")

    status_color = STATUS_COLORS.get(overall_status, (0, 0, 0))

    pdf = ReportPDF()
    pdf.generated_date_str = now.strftime("%Y-%m-%d")
    pdf.alias_nb_pages()
    pdf.set_margins(MARGIN_MM, MARGIN_MM, MARGIN_MM)
    pdf.set_auto_page_break(auto=True, margin=MARGIN_MM)
    pdf.add_page()

    right_edge = PAGE_WIDTH_MM - MARGIN_MM

    # ---- HEADER (compressed so it never overflows page 1) ----
    pdf.set_font("Helvetica", "B", FONT_TITLE)
    pdf.cell(0, 11, "Clearstone Inspect", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.set_font("Helvetica", "", FONT_BODY)
    pdf.cell(0, 6, "Inspection Report", new_x="LMARGIN", new_y="NEXT", align="C")
    pdf.ln(2)

    # ---- OVERALL STATUS BADGE (large, centered, color-coded) ----
    pdf.set_fill_color(*status_color)
    pdf.set_text_color(255, 255, 255)
    pdf.set_font("Helvetica", "B", FONT_TITLE)
    pdf.cell(0, 14, overall_status, new_x="LMARGIN", new_y="NEXT", align="C", fill=True)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(3)

    pdf.set_draw_color(0, 0, 0)
    pdf.line(MARGIN_MM, pdf.get_y(), right_edge, pdf.get_y())
    pdf.ln(4)

    pdf.set_font("Helvetica", "", FONT_BODY)
    pdf.cell(35, 6, "Project:", new_x="RIGHT")
    pdf.cell(0, 6, project_name, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(35, 6, "Address:", new_x="RIGHT")
    pdf.cell(0, 6, address, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(35, 6, "Inspector:", new_x="RIGHT")
    pdf.cell(0, 6, inspector_name, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(35, 6, "Date:", new_x="RIGHT")
    pdf.cell(0, 6, now.strftime("%Y-%m-%d  %H:%M:%S"), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # ---- AI INSPECTION SUMMARY (TLDR) — light stone background box ----
    ai_summary = generate_ai_summary(findings, transcript_segments)
    if ai_summary:
        pdf.set_font("Helvetica", "B", FONT_SECTION)
        pdf.cell(0, 8, "AI Inspection Summary", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

        pdf.set_font("Helvetica", "", FONT_BODY)
        box_left = pdf.l_margin
        pdf.set_fill_color(*STONE_BG)
        pdf.set_x(box_left + 2)
        pdf.multi_cell(CONTENT_WIDTH_MM - 4, 6, ai_summary, fill=True)
        pdf.ln(1)

        pdf.set_font("Helvetica", "I", FONT_CAPTION)
        pdf.set_text_color(120, 120, 120)
        pdf.set_fill_color(*STONE_BG)
        pdf.set_x(box_left + 2)
        pdf.multi_cell(
            CONTENT_WIDTH_MM - 4, 4.5,
            "Generated by Clearstone AI - verify all findings with a licensed inspector",
            fill=True,
        )
        pdf.set_text_color(0, 0, 0)
        pdf.ln(3)

    # ---- SUMMARY ----
    pdf.line(MARGIN_MM, pdf.get_y(), right_edge, pdf.get_y())
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", FONT_SECTION)
    pdf.cell(0, 8, "Summary", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)

    pdf.set_font("Helvetica", "", FONT_BODY)
    pdf.cell(0, 6, f"Total findings: {total}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"FAIL: {fail_count}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"CAUTION: {caution_count}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"PASS: {pass_count}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # ---- FINDINGS (or "No Violations Identified" when the walkthrough is clean) ----
    pdf.line(MARGIN_MM, pdf.get_y(), right_edge, pdf.get_y())
    pdf.ln(4)

    if not findings:
        pdf.set_font("Helvetica", "B", FONT_SECTION)
        pdf.cell(0, 8, "No Violations Identified", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        pdf.set_font("Helvetica", "", FONT_BODY)
        pdf.multi_cell(
            0, 6,
            "This walkthrough was analyzed and no violations were "
            "identified. The full transcript is included below for "
            "reference."
        )
        pdf.ln(3)
    else:
        pdf.set_font("Helvetica", "B", FONT_SECTION)
        pdf.cell(0, 8, "Findings", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

        for i, finding in enumerate(findings, 1):
            _render_finding_card(pdf, i, finding)

    # ---- TRANSCRIPT ----
    _render_transcript_section(pdf, transcript_segments)

    # ---- SIGN OFF (always its own final page) ----
    pdf.add_page()
    pdf.set_font("Helvetica", "B", FONT_SECTION)
    pdf.cell(0, 8, "Sign Off", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.set_font("Helvetica", "", FONT_BODY)
    pdf.cell(35, 6, "Inspector:", new_x="RIGHT")
    pdf.cell(0, 6, inspector_name, new_x="LMARGIN", new_y="NEXT")
    pdf.cell(35, 6, "Date:", new_x="RIGHT")
    pdf.cell(0, 6, now.strftime("%Y-%m-%d"), new_x="LMARGIN", new_y="NEXT")

    if notes:
        pdf.cell(35, 6, "Notes:", new_x="RIGHT")
        pdf.multi_cell(0, 6, notes)

    pdf.ln(10)
    pdf.cell(35, 6, "Signature:", new_x="RIGHT")
    pdf.cell(80, 6, "", border="B")
    pdf.ln(4)

    job_output_dir = ensure_dir(os.path.join("jobs", job_id, "output"))
    report_filename = os.path.join(
        job_output_dir, f"inspection_report_{job_id}.pdf"
    )
    pdf.output(report_filename)

    print(f"\nReport saved to: {report_filename}")
    return report_filename


def prompt_and_generate():
    print()
    job_id = input("Job ID: ").strip()
    project_name = input("Project name: ").strip()
    address = input("Project address: ").strip()
    inspector_name = input("Inspector name: ").strip()
    notes = input("Additional notes (or press Enter to skip): ").strip()
    print()
    generate_report(job_id, project_name, address, inspector_name, notes)


if __name__ == "__main__":
    prompt_and_generate()
