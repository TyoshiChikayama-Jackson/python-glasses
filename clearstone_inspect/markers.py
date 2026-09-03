"""
Reads a job's transcript and identifies moments of interest — markers —
worth cross-referencing against visual evidence:

1. "explicit" markers: any segment where the inspector says "Petra" —
   a deliberate, high-precision spoken flag, given the highest weight.
   This is a shortcut, never a requirement: most real recordings won't
   use it, and the keyword vocabulary below is what actually carries
   detection.
2. "keyword" markers: any segment containing a violation-related term,
   either pulled from violations.json (name + visual_indicators) or the
   general construction-issue vocabulary — spoken conditions,
   judgments, and trade/system terms an inspector would naturally use
   while describing a problem, even without ever saying "Petra".

A segment that matches a keyword is skipped (no marker created) if it
also contains a "clear" phrase near that keyword (e.g. "the wiring
looks good") — see _is_negated().
"""

import json
import os
import re

WAKE_WORD = "petra"

# General construction-issue vocabulary — catches spoken concerns even
# when they don't match a specific violations.json entry. Organized by
# category for readability; find_markers() only cares about the flat
# set below.
CONDITION_KEYWORDS = [
    "exposed", "missing", "cracked", "loose", "damaged", "unsecured",
    "uncapped", "leaking", "rusted", "rotted", "warped", "bent",
    "broken", "split", "gap", "gaps", "hole", "uneven", "sagging",
    "bowed", "out of plumb", "out of level", "not sealed", "unsealed",
    "improperly", "incorrectly", "temporary",
]

JUDGMENT_KEYWORDS = [
    "violation", "hazard", "unsafe", "fails", "failed", "not to code",
    "code issue", "needs replacing", "needs repair", "needs to be redone",
    "has to come out", "won't pass", "red flag", "problem here",
    "issue here", "concern", "deficiency", "punch list", "callback",
]

TRADE_SYSTEM_KEYWORDS = [
    "wiring", "wire", "cable", "conduit", "junction box", "outlet",
    "receptacle", "panel", "breaker", "grounding", "plumbing", "drain",
    "supply line", "p-trap", "vent stack", "hvac", "ductwork", "framing",
    "stud", "joist", "header", "rafter", "truss", "drywall", "insulation",
    "vapor barrier", "flashing", "roofing", "shingle", "subfloor",
    "footing", "foundation", "grading", "egress", "handrail", "guardrail",
    "riser", "tread", "firestop", "fire block",
]

GENERAL_ISSUE_KEYWORDS = (
    CONDITION_KEYWORDS + JUDGMENT_KEYWORDS + TRADE_SYSTEM_KEYWORDS
)

# Phrases that mean "this is fine" — if one of these appears in the same
# segment as a matched keyword, the keyword is treated as cleared rather
# than flagged. This stops "the wiring looks good" from being read the
# same as "the wiring is exposed".
CLEAR_PHRASES = [
    "looks good", "no issues", "no issue", "that's fine", "thats fine",
    "passes", "all set", "clean", "no problem",
]


def _is_negated(text_lower):
    """Returns True if text_lower contains a "this is fine" phrase,
    meaning any keyword match in this same segment should be treated as
    cleared rather than flagged."""
    return any(phrase in text_lower for phrase in CLEAR_PHRASES)


def load_violation_keywords(violations_path="violations.json"):
    """
    Builds the keyword vocabulary from violations.json: each violation's
    name plus every visual_indicators entry, lowercased. Returns a list
    of (keyword, violation_dict) pairs so a matched keyword can be
    traced back to which violation it came from.
    """
    if not os.path.exists(violations_path):
        return []

    with open(violations_path, "r") as f:
        data = json.load(f)

    pairs = []
    for violation in data.get("violations", []):
        pairs.append((violation["name"].lower(), violation))
        for indicator in violation.get("visual_indicators", []):
            pairs.append((indicator.lower(), violation))

    return pairs


def _contains_word_or_phrase(text_lower, term):
    """
    Matches term as a whole word/phrase within text_lower, not as a
    substring of an unrelated word (e.g. "hazard" shouldn't match inside
    some longer unrelated token). Uses a simple regex word boundary,
    which works fine for the short terms used here.
    """
    pattern = r"\b" + re.escape(term) + r"\b"
    return re.search(pattern, text_lower) is not None


def find_markers(job_id=None, segments=None, violations_path="violations.json"):
    """
    Identifies markers in a transcript's segments. Provide either job_id
    (to load jobs/<job_id>/transcript.json) or segments directly (a list
    of {start, end, text} dicts, e.g. from transcribe.transcribe_job()).

    Returns a list of markers, each: {timestamp, text, type, weight}.
    - type "explicit": the segment mentions "Petra". weight 3.
    - type "keyword": the segment contains a violation/issue keyword.
      weight 2 if matched against a violations.json term (also carries
      "violation_id" so correlate.py can look it up directly), weight 1
      if matched only against the general vocabulary.

    timestamp is the segment's start time in seconds. If a segment
    matches both the wake word and a keyword, it's still reported once,
    as the higher-weight "explicit" marker — the keyword match is
    implied by the same excerpt being available to correlate.py.
    """
    if segments is None:
        if job_id is None:
            raise ValueError("Provide either job_id or segments.")
        transcript_path = os.path.join("jobs", job_id, "transcript.json")
        with open(transcript_path, "r") as f:
            transcript = json.load(f)
        segments = transcript.get("segments", [])

    violation_keywords = load_violation_keywords(violations_path)

    markers = []
    for seg in segments:
        text = seg.get("text", "")
        text_lower = text.lower()
        start = seg.get("start", 0)

        if _contains_word_or_phrase(text_lower, WAKE_WORD):
            markers.append({
                "timestamp": start,
                "text": text.strip(),
                "type": "explicit",
                "weight": 3,
            })
            continue

        if _is_negated(text_lower):
            # A "this is fine" phrase is present in this segment — treat
            # any keyword match here as cleared, not flagged (e.g. "the
            # wiring looks good" should not create a marker).
            continue

        matched_violation = None
        for keyword, violation in violation_keywords:
            if _contains_word_or_phrase(text_lower, keyword):
                matched_violation = violation
                break

        if matched_violation:
            markers.append({
                "timestamp": start,
                "text": text.strip(),
                "type": "keyword",
                "weight": 2,
                "violation_id": matched_violation["id"],
            })
            continue

        for keyword in GENERAL_ISSUE_KEYWORDS:
            if _contains_word_or_phrase(text_lower, keyword):
                markers.append({
                    "timestamp": start,
                    "text": text.strip(),
                    "type": "keyword",
                    "weight": 1,
                })
                break

    return markers


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python markers.py <job_id>")
        sys.exit(1)

    for m in find_markers(job_id=sys.argv[1]):
        print(m)
