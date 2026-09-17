"""
Offline token-efficiency auditor (roadmap D-190).

Read-only diagnostic that scans the repository for places where agent/model
context is likely wasted, then produces ranked recommendations for reducing
future token consumption without reducing correctness.

What it measures (all deterministic, offline, no LLM calls):

- ``estimate_tokens``: documented approximation ``chars / 4`` (per
  .agent_prompts/token_efficiency_auditor.md). Never presented as an exact
  provider bill.
- ``duplicate_block`` findings: identical or near-identical instruction /
  prose paragraphs repeated across files (prompts, reports, docs, specs).
  Exact duplicates are detected across all scanned files; near-duplicates
  only among markdown (the prompt/doc material where redundant instructions
  live).
- ``oversized_file`` findings: files large enough that an agent must load
  the whole thing when a smaller targeted artifact would do.
- ``verbose_report`` findings: reports/*.md above a narrative-size baseline
  (excessive prose instead of compact evidence).
- ``long_task_spec`` findings: task-spec files large enough to suggest
  overly broad tasks that force repository-wide context loading.

Findings are ranked by **expected useful-context savings** (not file size),
per the auditor spec. Each finding carries a fix tier: ``D-safe`` (a
mechanical change a delegated agent may implement) or ``primary_claude``
(a change needing Primary Claude / human judgment).

Safety boundaries (never violated):

- READ-ONLY: no file is ever modified, deleted, or rewritten.
- No secrets: ``.env`` / ``*.key`` / ``*.pem`` / ``secrets/`` are excluded by
  default, and all evidence text is redacted for secret-looking values.
- No delegation decisions, no architecture/security/policy changes — the
  auditor only recommends; a separate reviewed task must implement any fix.
- Unreadable files are skipped and counted, never fatal.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from fnmatch import fnmatch
from pathlib import Path
from typing import Iterable

from opencode_crack.orchestrator.protocol_audit import split_blocks

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# --- documented estimation model -------------------------------------------------
TOKENS_PER_CHAR = 4

# --- detection thresholds (all overridable per call / CLI flag) ------------------
OVERSIZED_FILE_BYTES = 50_000          # medium-severity floor
OVERSIZED_FILE_HIGH_BYTES = 200_000    # high-severity floor
USEFUL_CONTEXT_BASELINE_BYTES = 25_000  # what a single file "should" usefully carry
VERBOSE_REPORT_BYTES = 8_000           # reports above this are narrative-heavy
LONG_SPEC_BYTES = 20_000               # task-spec files above this are too broad
DUPLICATE_HIGH_TOKENS = 300            # duplicate block tokens for high severity
DEFAULT_MIN_CHARS = 120                # duplicate-block size floor (protocol_audit)
DEFAULT_SIMILARITY = 0.9               # near-duplicate threshold

# --- default exclusions: generated artifacts, secrets, user data ------------------
# Directory names (matched on any path component) and file globs matched
# against the slash-normalized relative path. Mirrors .gitignore plus the
# auditor's safety boundaries (never scan or expose secrets/user data).
DEFAULT_EXCLUDED_DIRS = (
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".cache",
    "node_modules", "data", "orchestration", ".swarm", "secrets",
    # Derived/environment content that is never project prose (the
    # real-repo audit hung scanning 16k files, 94% of them here):
    # IDE runtime state, disposable worktrees / nested checkouts.
    ".kilo", ".swarm-worktrees-local", "test-worktree", "OpenCode-Crack",
)
# Directory-name prefixes (any path component) for families like
# venv_chromadb_0_5 / venv_chromadb_159_py312 — present and future
# alternate venvs (site-packages, not project content).
DEFAULT_EXCLUDED_DIR_PREFIXES = ("venv_",)
DEFAULT_EXCLUDED_PATTERNS = (
    "*.pyc", "*.pyo", "*.db", "*.sqlite", "*.sqlite3", "*.log", "*.json",
    "*.csv", "*.png", "*.jpg", "*.jpeg", "*.gif", "*.pdf", "*.docx",
    "*.xlsx", "*.zip", "*.gz", "*.tar", "*.lock", "*.pem", "*.key",
    ".env", ".env.*", "*.env",
)

SECRET_VALUE_PATTERN = re.compile(
    r"(?i)((?:api[_-]?key|secret|password|passwd|token|credential|"
    r"authorization|private[_-]?key)\s*[=:]\s*)(\S+)"
)

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass(frozen=True)
class Finding:
    """One ranked finding with evidence and expected savings."""
    kind: str
    severity: str
    file: str
    evidence: str
    why: str
    recommendation: str
    estimated_tokens: int
    estimated_savings_tokens: int
    confidence: str
    fix_tier: str


@dataclass
class AuditReport:
    """Result of one audit run (summary metrics + ranked findings)."""
    root: str
    scanned_files: int
    excluded_files: int
    unreadable_files: int
    total_bytes: int
    total_tokens: int
    findings: list[Finding] = field(default_factory=list)
    largest_files: list[dict] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    """Documented approximation: ``chars / 4``. Deterministic, offline."""
    return len(text) // TOKENS_PER_CHAR


def _redact(text: str) -> str:
    """Mask secret-looking values (``key=value`` / ``key: value``) so
    evidence excerpts can never expose credentials."""
    return SECRET_VALUE_PATTERN.sub(r"\1***", text)


def _is_excluded(rel_path: str, extra_excludes: Iterable[str] = ()) -> bool:
    """True when a slash-normalized relative path matches a default or
    user-supplied exclusion (directory name or glob pattern)."""
    parts = rel_path.split("/")
    if any(part in DEFAULT_EXCLUDED_DIRS for part in parts):
        return True
    if any(part.startswith(DEFAULT_EXCLUDED_DIR_PREFIXES) for part in parts):
        return True
    for pattern in DEFAULT_EXCLUDED_PATTERNS:
        if fnmatch(rel_path, pattern):
            return True
    for pattern in extra_excludes:
        if fnmatch(rel_path, pattern) or any(fnmatch(part, pattern) for part in parts):
            return True
    return False


def discover_files(
    root: Path, extra_excludes: Iterable[str] = ()
) -> tuple[list[Path], int]:
    """Walk ``root`` and return (included files, excluded-file count).

    Excluded directories are pruned during the walk; excluded files are
    counted so the summary shows how much was intentionally skipped.
    """
    included: list[Path] = []
    excluded_count = 0
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirpath = Path(dirpath)
        kept_dirs = []
        for name in dirnames:
            rel_dir = (dirpath / name).relative_to(root).as_posix()
            if _is_excluded(rel_dir, extra_excludes):
                excluded_count += 1
            else:
                kept_dirs.append(name)
        dirnames[:] = kept_dirs
        for name in filenames:
            rel_file = (dirpath / name).relative_to(root).as_posix()
            if _is_excluded(rel_file, extra_excludes):
                excluded_count += 1
            else:
                included.append(dirpath / name)
    return included, excluded_count


def _read_text(path: Path) -> str:
    """Read a file as UTF-8 with replacement errors (binary-ish content
    degrades instead of crashing). Separate helper so tests can force
    read failures for the unreadable-file path."""
    return path.read_text(encoding="utf-8", errors="replace")


def load_files(
    files: Iterable[Path], root: Path | str
) -> tuple[list[tuple[str, str]], int]:
    """Read files as (relative-path, text) pairs, skipping unreadable ones.

    Returns (entries, unreadable_count). Read-only: failures are counted,
    never fatal. Relative paths (slash-normalized) are used so findings
    point at the same location regardless of where the audit ran from.
    """
    root_path = Path(root)
    entries: list[tuple[str, str]] = []
    unreadable = 0
    for path in files:
        try:
            text = _read_text(path)
        except (OSError, UnicodeDecodeError):
            unreadable += 1
            continue
        rel = path.relative_to(root_path).as_posix()
        entries.append((rel, text))
    return entries, unreadable


def _fix_tier_for_files(files: Iterable[str]) -> str:
    """Duplicate removal needs Primary Claude when it touches protocol
    documents or production code; mechanical dedupe in reports/task specs
    is safe for a D-tier agent."""
    for rel in files:
        if (rel.startswith(".agent_prompts/") or rel.startswith("AGENTS.md")
                or rel.startswith("src/") or rel.startswith("docs/")):
            return "primary_claude"
    return "D-safe"


def find_duplicate_hits(
    entries: list[tuple[str, str]],
    min_chars: int = DEFAULT_MIN_CHARS,
    similarity: float = DEFAULT_SIMILARITY,
) -> list[Finding]:
    """Detect duplicated prose blocks across files.

    Exact duplicates (normalized text equal) are grouped by hash across ALL
    scanned files. Near-duplicates (SequenceMatcher ratio >= ``similarity``)
    are scanned pairwise among markdown files only, bucketed by length with
    a shared-vocabulary prefilter and a ``quick_ratio`` upper-bound gate
    (both cheaper than the full ratio, neither changes results).

    Blocks that belong together (exact or near) are merged into clusters
    (union-find), and ONE finding is emitted per cluster listing every file
    that carries the block. Same-file repeats are intentionally ignored
    (in-file repetition is often intentional structure).
    """
    blocks: list[tuple[str, str]] = []
    for rel, text in entries:
        for block in split_blocks(text, min_chars=min_chars):
            blocks.append((rel, block))

    # cluster ids: parent[i] = i initially
    n = len(blocks)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    # exact duplicates across ALL files, by normalized text
    by_text: dict[str, list[int]] = {}
    for idx, (_, block) in enumerate(blocks):
        by_text.setdefault(block, []).append(idx)
    for group in by_text.values():
        files_with = {blocks[i][0] for i in group}
        if len(files_with) < 2:
            continue
        for i in group[1:]:
            union(group[0], i)

    # near-duplicates among markdown (prompt/doc material) only
    md_indices = [i for i, (rel, _) in enumerate(blocks) if rel.endswith(".md")]
    buckets: dict[int, list[int]] = {}
    for i in md_indices:
        buckets.setdefault(len(blocks[i][1]) // 40, []).append(i)

    for group in buckets.values():
        # Cheap prefilters: shared vocabulary (>= 5 words) and the quick_ratio
        # upper bound — SequenceMatcher's full ratio() only runs on pairs that
        # can actually pass the threshold, keeping the scan fast on large doc
        # sets without changing results.
        token_sets = [
            frozenset(re.findall(r"[a-z0-9]{3,}", blocks[i][1])) for i in group
        ]
        for a_pos, i in enumerate(group):
            rel_a, block_a = blocks[i]
            tokens_a = token_sets[a_pos]
            for b_pos in range(a_pos + 1, len(group)):
                j = group[b_pos]
                rel_b, block_b = blocks[j]
                if rel_a == rel_b:
                    continue
                shared = len(tokens_a & token_sets[b_pos])
                # Cheap prefilter: a real near-duplicate (SequenceMatcher
                # ratio >= `similarity`) must share most of its vocabulary,
                # so blocks sharing few words or a low token-overlap can
                # never qualify. This keeps the expensive ratio() call to a
                # tiny candidate set without changing results.
                if shared < 5:
                    continue
                overlap = 2 * shared / (len(tokens_a) + len(token_sets[b_pos]))
                if overlap < 0.6:
                    continue
                if SequenceMatcher(None, block_a, block_b).ratio() >= similarity:
                    union(i, j)

    # one finding per cluster of >= 2 distinct files
    clusters: dict[int, list[int]] = {}
    for i in range(n):
        clusters.setdefault(find(i), []).append(i)

    findings: list[Finding] = []
    for root_idx, members in clusters.items():
        files_with = sorted({blocks[i][0] for i in members})
        if len(files_with) < 2:
            continue
        block = blocks[root_idx][1]
        tokens = estimate_tokens(block)
        copies = len(files_with)
        severity = "high" if tokens >= DUPLICATE_HIGH_TOKENS else "medium"
        shown = ", ".join(files_with[:4])
        extra = f" (+{copies - 4} more)" if copies > 4 else ""
        findings.append(Finding(
            kind="duplicate_block",
            severity=severity,
            file=files_with[0],
            evidence=(
                f"block (~{tokens} tokens) duplicated across {copies} "
                f"file(s): {shown}{extra}"
            ),
            why="every agent that loads one of these files loads the same "
                "instruction text again, wasting context per copy",
            recommendation="remove the redundant copies; keep one canonical "
                           "location and reference it",
            estimated_tokens=tokens * copies,
            estimated_savings_tokens=tokens * (copies - 1),
            confidence="high" if len(by_text.get(block, [])) > 1 else "medium",
            fix_tier=_fix_tier_for_files(files_with),
        ))
    return findings


def analyze_oversized_files(
    entries: list[tuple[str, str]],
) -> list[Finding]:
    """Flag files large enough that loading them whole wastes context when
    a smaller targeted artifact would suffice."""
    findings: list[Finding] = []
    for rel, text in entries:
        size = len(text.encode("utf-8", errors="replace"))
        if size < OVERSIZED_FILE_BYTES:
            continue
        tokens = estimate_tokens(text)
        baseline = USEFUL_CONTEXT_BASELINE_BYTES // TOKENS_PER_CHAR
        savings = max(0, tokens - baseline)
        severity = "high" if size >= OVERSIZED_FILE_HIGH_BYTES else "medium"
        findings.append(Finding(
            kind="oversized_file",
            severity=severity,
            file=rel,
            evidence=f"{size} bytes, ~{tokens} tokens",
            why="agents needing a small piece of this file must load all of "
                "it into context",
            recommendation="split into smaller targeted artifacts or provide "
                           "a compact summary/index so agents fetch only "
                           "what they need",
            estimated_tokens=tokens,
            estimated_savings_tokens=savings,
            confidence="medium",
            fix_tier="primary_claude",
        ))
    return findings


def analyze_verbose_reports(entries: list[tuple[str, str]]) -> list[Finding]:
    """Flag reports/*.md whose narrative exceeds a compact-evidence baseline."""
    findings: list[Finding] = []
    for rel, text in entries:
        if not rel.startswith("reports/") or not rel.endswith(".md"):
            continue
        size = len(text.encode("utf-8", errors="replace"))
        if size < VERBOSE_REPORT_BYTES:
            continue
        tokens = estimate_tokens(text)
        savings = max(0, tokens - VERBOSE_REPORT_BYTES // TOKENS_PER_CHAR)
        findings.append(Finding(
            kind="verbose_report",
            severity="low",
            file=rel,
            evidence=f"{size} bytes, ~{tokens} tokens",
            why="reports are read by reviewers; long narrative buries the "
                "compact evidence the 5-point format asks for",
            recommendation="trim narrative to the 5-point format "
                           "(summary / files / tests / failures / blockers)",
            estimated_tokens=tokens,
            estimated_savings_tokens=savings,
            confidence="low",
            fix_tier="D-safe",
        ))
    return findings


def analyze_task_specs(entries: list[tuple[str, str]]) -> list[Finding]:
    """Flag task-spec files large enough to suggest overly broad tasks."""
    findings: list[Finding] = []
    for rel, text in entries:
        is_spec = (
            rel.startswith("delegated_tasks/")
            or rel == "docs/iteration improvement ideas.md"
        )
        if not is_spec:
            continue
        size = len(text.encode("utf-8", errors="replace"))
        if size < LONG_SPEC_BYTES:
            continue
        tokens = estimate_tokens(text)
        savings = max(0, tokens - LONG_SPEC_BYTES // TOKENS_PER_CHAR)
        findings.append(Finding(
            kind="long_task_spec",
            severity="medium",
            file=rel,
            evidence=f"{size} bytes, ~{tokens} tokens",
            why="very long specs force agents to load broad context; scope "
                "this broad usually means the task is several tasks",
            recommendation="extract bounded sub-tasks via the task-breakdown "
                           "protocol instead of one giant spec",
            estimated_tokens=tokens,
            estimated_savings_tokens=savings,
            confidence="low",
            fix_tier="primary_claude",
        ))
    return findings


def rank_findings(findings: Iterable[Finding]) -> list[Finding]:
    """Rank by expected useful-context savings (spec requirement), not by
    file size. Stable: ties keep detection order."""
    return sorted(
        findings,
        key=lambda f: (f.estimated_savings_tokens, f.estimated_tokens),
        reverse=True,
    )


def run_audit(
    root: Path | str = REPO_ROOT,
    extra_excludes: Iterable[str] = (),
    min_chars: int = DEFAULT_MIN_CHARS,
    similarity: float = DEFAULT_SIMILARITY,
) -> AuditReport:
    """Full audit: discover -> read -> analyze -> rank. Read-only end to end."""
    files, excluded = discover_files(Path(root), extra_excludes=extra_excludes)
    entries, unreadable = load_files(files, root)

    findings: list[Finding] = []
    findings.extend(find_duplicate_hits(entries, min_chars=min_chars,
                                        similarity=similarity))
    findings.extend(analyze_oversized_files(entries))
    findings.extend(analyze_verbose_reports(entries))
    findings.extend(analyze_task_specs(entries))

    total_bytes = sum(len(t.encode("utf-8", errors="replace")) for _, t in entries)
    total_tokens = sum(estimate_tokens(t) for _, t in entries)

    largest = sorted(
        (
            {"file": rel, "bytes": len(t.encode("utf-8", errors="replace")),
             "tokens": estimate_tokens(t)}
            for rel, t in entries
        ),
        key=lambda row: row["tokens"],
        reverse=True,
    )[:10]

    return AuditReport(
        root=str(Path(root)),
        scanned_files=len(entries),
        excluded_files=excluded,
        unreadable_files=unreadable,
        total_bytes=total_bytes,
        total_tokens=total_tokens,
        findings=rank_findings(findings),
        largest_files=largest,
    )


def _excerpt(text: str, width: int = 140) -> str:
    """Short redacted excerpt of evidence text for human output."""
    text = _redact(" ".join(text.split()))
    return text if len(text) <= width else text[:width] + "…"


def render_report(report: AuditReport) -> str:
    """Concise human-readable report: summary + ranked findings."""
    lines = [
        f"Token-efficiency audit: {report.root}",
        f"  {report.scanned_files} file(s) scanned, {report.excluded_files} "
        f"excluded, {report.unreadable_files} unreadable (skipped)",
        f"  ~{report.total_tokens} tokens across {report.total_bytes} bytes "
        f"(estimate: chars / {TOKENS_PER_CHAR})",
    ]
    if not report.findings:
        lines.append("  No waste findings — nothing to report.")
        return "\n".join(lines)

    lines.append(f"  {len(report.findings)} finding(s), ranked by expected savings:")
    for idx, f in enumerate(report.findings, 1):
        lines.append(
            f"{idx}. [{f.severity}] {f.kind} — save ~{f.estimated_savings_tokens} "
            f"tokens ({f.confidence} confidence, fix: {f.fix_tier})"
        )
        lines.append(f"   {f.file}")
        lines.append(f"   {_excerpt(f.evidence)}")
        lines.append(f"   {_excerpt(f.recommendation)}")
    lines.append(
        "Read-only report — nothing was changed. Token counts are "
        f"chars/{TOKENS_PER_CHAR} estimates, not provider bills."
    )
    return "\n".join(lines)


def report_to_dict(report: AuditReport) -> dict:
    """Machine-readable JSON-able structure of the full report."""
    return {
        "root": report.root,
        "scanned_files": report.scanned_files,
        "excluded_files": report.excluded_files,
        "unreadable_files": report.unreadable_files,
        "total_bytes": report.total_bytes,
        "total_tokens": report.total_tokens,
        "estimation_method": f"chars / {TOKENS_PER_CHAR} (approximation)",
        "largest_files": report.largest_files,
        "findings": [
            {
                "kind": f.kind,
                "severity": f.severity,
                "file": f.file,
                "evidence": _redact(f.evidence),
                "why": f.why,
                "recommendation": f.recommendation,
                "estimated_tokens": f.estimated_tokens,
                "estimated_savings_tokens": f.estimated_savings_tokens,
                "confidence": f.confidence,
                "fix_tier": f.fix_tier,
            }
            for f in report.findings
        ],
    }


def render_json(report: AuditReport) -> str:
    """Deterministic JSON serialization (stable key order, indent=2)."""
    return json.dumps(report_to_dict(report), indent=2, sort_keys=True)