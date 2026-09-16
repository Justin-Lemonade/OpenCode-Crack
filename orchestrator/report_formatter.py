"""Standard concise report formatter (roadmap D-154).

Turns a delegated task's report into a compact, fact-first form bounded by
a configurable character/token cap. The structured fields from the
report_schema (D-153) are preserved exactly — paths, test commands and
results, failures, blockers, task ID, outcome/status, and git state are
never truncated. Only low-value prose (summary, environment, notes) is
compacted and, when needed, truncated to bring the whole report under the
cap.

Compaction rules (deterministic, no model involved):
- runs of blank lines collapse to one;
- exact duplicate lines are deduplicated (first occurrence kept);
- each prose field collapses internal whitespace;
- if the report still exceeds the cap after compaction, prose fields are
  truncated at a sentence boundary with an explicit "[truncated]" marker —
  never the protected fields;
- if the protected fields alone exceed the cap, they are still emitted in
  full and the report simply runs long (protected > cap is a report that
  needs a smaller cap, not a license to drop facts).

The cap is character-equivalent; a token-equivalent cap is converted with
a documented CHARS_PER_TOKEN constant so callers can pass max_tokens too.
"""
from __future__ import annotations

import re

# Common token approximation: ~4 characters per token for English prose.
CHARS_PER_TOKEN = 4

DEFAULT_MAX_CHARS = 2000

_TRUNCATION_MARKER = " [truncated]"

# Structured fields rendered verbatim — never compacted or truncated.
_FACT_FIELDS = ("files_changed", "tests", "failures", "blockers")

# Status fields emitted in the header line — never truncated.
_STATUS_FIELDS = ("outcome", "confidence", "source")

# Free-text prose fields — compacted first, truncated last.
_PROSE_FIELDS = ("summary", "environment", "notes")


def resolve_cap(max_chars: "int | None" = None, max_tokens: "int | None" = None) -> int:
    """Resolve a character cap from an explicit max_chars or max_tokens.

    max_tokens is converted with CHARS_PER_TOKEN. When both are given,
    max_chars wins (it is the more precise of the two). When neither is
    given, DEFAULT_MAX_CHARS applies.
    """
    if max_chars is not None:
        return max(1, int(max_chars))
    if max_tokens is not None:
        return max(1, int(max_tokens) * CHARS_PER_TOKEN)
    return DEFAULT_MAX_CHARS


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n\s*\n+", "\n", text.strip())


def _dedupe_sentences(text: str) -> str:
    """Drop exact duplicate sentences, keeping the first occurrence.

    Repetitive prose in reports is usually a phrase restated across
    sections; deduplicating sentences removes the echo without touching
    the structured facts (which never pass through this compaction).
    """
    sentences = [
        re.sub(r"\s+", " ", s).strip()
        for s in _SENTENCE_SPLIT_RE.split(text)
        if s.strip()
    ]
    seen: set[str] = set()
    kept: list[str] = []
    for sentence in sentences:
        key = sentence.casefold()
        if key in seen:
            continue
        seen.add(key)
        kept.append(sentence)
    return " ".join(kept)


def _compact_prose(text: str) -> str:
    return _dedupe_sentences(_collapse_blank_lines(text))


def _truncate_prose(text: str, budget: int) -> str:
    """Truncate prose to fit `budget` characters at a sentence boundary.

    The explicit "[truncated]" marker is reserved inside the budget so the
    result never silently runs past the caller's limit.
    """
    if len(text) <= budget:
        return text
    available = max(0, budget - len(_TRUNCATION_MARKER))
    if available <= 0:
        return _TRUNCATION_MARKER.strip()
    candidate = text[:available]
    # Cut back to the last sentence terminator within the budget so we
    # never emit a dangling half-sentence.
    for terminator in (". ", "! ", "? "):
        cut = candidate.rfind(terminator)
        if cut > 0:
            candidate = candidate[: cut + 1]
            break
    return candidate.strip() + _TRUNCATION_MARKER


def _render_report(report: dict) -> str:
    """Render the verbatim (protected) part of a report."""
    lines: list[str] = []
    if report.get("task_id"):
        lines.append(f"{report['task_id']}")
    status_bits = []
    for key in _STATUS_FIELDS:
        if report.get(key):
            status_bits.append(f"{key}: {report[key]}")
    if report.get("git_state"):
        status_bits.append(f"git_state: {report['git_state']}")
    if status_bits:
        lines.append(" | ".join(status_bits))
    for field in _FACT_FIELDS:
        value = report.get(field)
        if value is None or (isinstance(value, list) and not value):
            continue
        if isinstance(value, list):
            lines.append(f"{field}:")
            lines.extend(f"- {item}" for item in value)
        else:
            lines.append(f"{field}: {value}")
    return "\n".join(lines)


def format_report(
    report: dict,
    max_chars: "int | None" = None,
    max_tokens: "int | None" = None,
) -> str:
    """Render a report dict as a compact, fact-first block bounded by a cap.

    Structured fields are emitted verbatim. Prose fields are compacted and
    then truncated (in a deterministic order) only as needed to meet the
    cap. task ID, outcome/status, git state, failures, and blockers are
    never truncated.
    """
    cap = resolve_cap(max_chars, max_tokens)

    protected = _render_report(report)
    prose = {
        field: _compact_prose(report[field])
        for field in _PROSE_FIELDS
        if report.get(field)
    }

    # Compact-only pass first: this alone usually brings the report under cap.
    full = protected
    for field, text in prose.items():
        if text:
            full += "\n\n" + f"{field}: {text}"
    if len(full) <= cap:
        return full

    # Truncation needed. Protected fields stay verbatim; prose gets whatever
    # budget remains, in declaration order (summary first).
    if len(protected) > cap:
        return protected
    result = protected
    remaining = cap - len(protected)
    for field in _PROSE_FIELDS:
        text = prose.get(field)
        if not text:
            continue
        prefix = f"\n\n{field}: "
        if remaining <= len(prefix):
            break  # not enough room even for the label; drop the field
        if len(prefix) + len(text) <= remaining:
            result += prefix + text
            remaining -= len(prefix) + len(text)
        else:
            budget = remaining - len(prefix)
            result += prefix + _truncate_prose(text, budget)
            remaining = 0
    return result