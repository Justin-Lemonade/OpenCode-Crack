"""
Static protocol redundancy audit (roadmap D-159, from the token-efficiency
audit of 2026-08-19).

Detects duplicate or near-duplicate workflow-instruction blocks repeated
across the agent-protocol documents:

    AGENTS.md
    .agent_prompts/find_work.md
    .agent_prompts/review_work.md
    .agent_prompts/task_breakdown.md
    the delegation contract template (src/orchestrator/delegator.py)

A "block" is a paragraph (contiguous non-blank lines) whose normalized text
is at least ``min_chars`` long. Two blocks are flagged when their normalized
text is byte-identical (exact duplicate) or textually similar above
``similarity_threshold`` (near-duplicate).

The audit is READ-ONLY: it reports source files and approximate duplicate
text but never deletes or rewrites anything, and it makes no claim that any
flagged block must be removed — that decision belongs to Primary Claude.

The comparison core is pure (``find_duplicates``) and takes explicit
``blocks`` so tests use tiny fixtures with known duplicates/non-duplicates
without touching the repo's real protocol files.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

DEFAULT_PROTOCOL_FILES = (
    REPO_ROOT / "docs" / "AGENTS.md",
    REPO_ROOT / ".agent_prompts" / "find_work.md",
    REPO_ROOT / ".agent_prompts" / "review_work.md",
    REPO_ROOT / ".agent_prompts" / "task_breakdown.md",
)

# Default duplicate size floor (in normalized characters) and similarity
# threshold for near-duplicate detection.
DEFAULT_MIN_CHARS = 120
DEFAULT_SIMILARITY = 0.9


@dataclass(frozen=True)
class Block:
    """One normalized instruction block from a source document."""
    source: str
    text: str


@dataclass(frozen=True)
class Duplicate:
    """A duplicate/near-duplicate block found in two source documents."""
    source: str
    other: str
    text: str
    similarity: float


def normalize_block(text: str) -> str:
    """Collapse whitespace/lowercase so wording-only differences in
    line wrapping don't mask real duplicates. Strips leading list markers."""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("- ", "* ", "  - ")):
            line = re.sub(r"^[-*]\s+", "", line)
        lines.append(line)
    return re.sub(r"\s+", " ", " ".join(lines)).strip().lower()


def split_blocks(markdown: str, min_chars: int = DEFAULT_MIN_CHARS) -> list[str]:
    """Split markdown into paragraphs, normalized; keep those at least
    ``min_chars`` long. Repeated instructions are typically full sentences
    or sentence groups, so short fragments are ignored."""
    paragraphs = re.split(r"\n\s*\n", markdown)
    out = []
    for para in paragraphs:
        text = normalize_block(para)
        if len(text) >= min_chars:
            out.append(text)
    return out


def find_duplicates(
    blocks: Iterable[Block],
    min_chars: int = DEFAULT_MIN_CHARS,
    similarity_threshold: float = DEFAULT_SIMILARITY,
) -> list[Duplicate]:
    """Pairwise, cross-document duplicate scan over pre-split blocks.

    Exact matches (normalized text equal) are reported once per source
    pair. Near-duplicates are reported when two blocks from DIFFERENT
    sources have a ``difflib`` ratio >= threshold and similar length.
    Same-source repeats are intentionally ignored (within a single doc
    repetition may be intentional structure). Read-only: returns hits only.
    """
    blocks = list(blocks)
    if len(blocks) < 2:
        return []

    hits: list[Duplicate] = []
    seen_exact: set[tuple[str, str]] = set()

    # exact duplicates, cross-document
    by_text: dict[str, list[Block]] = {}
    for b in blocks:
        by_text.setdefault(b.text, []).append(b)
    for text, group in by_text.items():
        if len(text) < min_chars or len(group) < 2:
            continue
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                if a.source == b.source:
                    continue
                key = frozenset((a.source, b.source))
                if key in seen_exact:
                    continue
                seen_exact.add(key)
                hits.append(Duplicate(a.source, b.source, a.text, 1.0))

    # near-duplicates, cross-document (skip pairs already exact-flagged)
    for i, a in enumerate(blocks):
        for j, b in enumerate(blocks):
            if i >= j or a.source == b.source:
                continue
            if (a.text == b.text or
                    frozenset((a.source, b.source)) in seen_exact):
                continue
            length_ratio = min(len(a.text), len(b.text)) / max(len(a.text), len(b.text))
            if length_ratio < 0.8:
                continue  # very different lengths are not near-duplicates
            ratio = SequenceMatcher(None, a.text, b.text).ratio()
            if ratio >= similarity_threshold:
                hits.append(Duplicate(a.source, b.source, a.text, round(ratio, 3)))
                seen_exact.add(frozenset((a.source, b.source)))

    return hits


def load_protocol_documents(
    files: Iterable[Path] = DEFAULT_PROTOCOL_FILES,
    include_contract_template: bool = True,
) -> list[tuple[str, str]]:
    """Load the protocol documents as (label, markdown) pairs.

    Also pulls the delegation contract template out of
    ``delegator.build_contract`` when ``include_contract_template`` (the
    generated delegation contract is the "delegation template" the audit
    targets). Missing files are skipped; present files are read as UTF-8.
    """
    docs: list[tuple[str, str]] = []
    for path in files:
        if Path(path).is_file():
            docs.append((str(path), Path(path).read_text(encoding="utf-8")))
    if include_contract_template:
        template = _delegation_contract_template()
        if template:
            docs.append(("delegation contract template (delegator.build_contract)", template))
    return docs


def _delegation_contract_template() -> Optional[str]:
    """Extract the raw template body from ``delegator.build_contract`` so the
    audit covers the contract that every delegated agent receives."""
    delegator = REPO_ROOT / "src" / "orchestrator" / "delegator.py"
    if not delegator.is_file():
        return None
    source = delegator.read_text(encoding="utf-8")
    match = re.search(r'return f"""(.*?)"""', source, re.DOTALL)
    if not match:
        return None
    return re.sub(r"\{[a-z_.]+\}", "", match.group(1))  # drop f-string placeholders


def audit_protocols(
    files: Iterable[Path] = DEFAULT_PROTOCOL_FILES,
    include_contract_template: bool = True,
    min_chars: int = DEFAULT_MIN_CHARS,
    similarity_threshold: float = DEFAULT_SIMILARITY,
) -> list[Duplicate]:
    """Full audit: load docs, split into blocks, scan for duplicates."""
    docs = load_protocol_documents(files, include_contract_template=include_contract_template)
    blocks = [
        Block(source=label, text=text)
        for label, markdown in docs
        for text in split_blocks(markdown, min_chars=min_chars)
    ]
    return find_duplicates(blocks, min_chars=min_chars,
                           similarity_threshold=similarity_threshold)


def _excerpt(text: str, width: int = 160) -> str:
    return text if len(text) <= width else text[:width] + "…"


def render_report(hits: list[Duplicate]) -> str:
    """Human-readable report of found duplicates. Read-only — reports only,
    never instructs deletion."""
    if not hits:
        return "Protocol redundancy audit: no duplicate instruction blocks found."
    lines = [
        f"Protocol redundancy audit: {len(hits)} duplicate/near-duplicate "
        f"instruction block(s) found.",
        "",
    ]
    for idx, hit in enumerate(hits, 1):
        lines.append(f"{idx}. similarity={hit.similarity:.3f}")
        lines.append(f"   {hit.source}")
        lines.append(f"   <-> {hit.other}")
        lines.append(f"   text: {_excerpt(hit.text)}")
        lines.append("")
    lines.append("Read-only report — no blocks were changed or removed.")
    return "\n".join(lines)