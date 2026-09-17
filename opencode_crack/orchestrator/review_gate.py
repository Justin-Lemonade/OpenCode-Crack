"""
Machine-checkable review gate (D-279).

Conservative policy: auto-approve only when ALL strict checks pass;
otherwise escalate to human/Primary Claude. Manual review remains
authoritative override — this gate never calls approve_task itself.

Explicit, testable — every decision branch is covered by tests.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Delegation rejection list — never auto-approve
PROTECTED_PATTERNS = [
    r"^src/brain\.py",
    r"^src/memory/",
    r"^src/models/(router|base|provider)",
    r"^src/tools/permissions\.py",
    r"^src/tools/registry\.py",
    r"^src/config\.py",
    r"^orchestration/tasks\.yaml",
    r"^\.opencode/",
]

# Security-sensitive — secrets, creds, auth
SENSITIVE_PATTERNS = [
    r"secret",
    r"credential",
    r"api_key",
    r"password",
    r"token",
    r"auth",
    r"\.env",
]

# Licensing-sensitive
LICENSING_PATTERNS = [
    r"^LICENSE",
    r"^requirements.*\.txt",
    r"^pyproject\.toml",
    r"licen[sc]e",
]

PROTECTED_RE = [re.compile(p, re.IGNORECASE) for p in PROTECTED_PATTERNS]
SENSITIVE_RE = [re.compile(p, re.IGNORECASE) for p in SENSITIVE_PATTERNS]
LICENSING_RE = [re.compile(p, re.IGNORECASE) for p in LICENSING_PATTERNS]


@dataclass(frozen=True)
class GateResult:
    verdict: str  # "auto_approve" | "escalate"
    reasons: tuple[str, ...]
    policy_version: str = "1.0"

    @property
    def auto_approvable(self) -> bool:
        return self.verdict == "auto_approve"


def _matches_any(path: str, patterns: list[re.Pattern]) -> Optional[re.Pattern]:
    for pat in patterns:
        if pat.search(path):
            return pat
    return None


def check_report_exists(report_path: Optional[str | Path]) -> tuple[bool, str]:
    if not report_path:
        return False, "missing_report: no report_path"
    p = Path(report_path)
    if not p.exists():
        return False, f"missing_report: {p} not found"
    if p.stat().st_size == 0:
        return False, f"missing_report: {p} empty"
    return True, ""


def check_tests_pass(test_results: Optional[dict]) -> tuple[bool, str]:
    if test_results is None:
        return False, "tests: no results"
    if test_results.get("failed", 1) != 0:
        return False, f"tests: {test_results.get('failed')} failed"
    if test_results.get("passed", 0) == 0 and test_results.get("skipped", 0) == 0:
        return False, "tests: no passing tests"
    return True, ""


def check_files_changed(changed_files: list[str]) -> tuple[bool, str]:
    if not changed_files:
        return True, ""  # allow report-only?
    for f in changed_files:
        m = _matches_any(f, PROTECTED_RE)
        if m:
            return False, f"protected_arch: {f} matches {m.pattern}"
        m = _matches_any(f, SENSITIVE_RE)
        if m:
            return False, f"security_sensitive: {f} matches {m.pattern}"
        m = _matches_any(f, LICENSING_RE)
        if m:
            return False, f"licensing_sensitive: {f} matches {m.pattern}"
    return True, ""


def check_expected_files_only(changed_files: list[str], expected_files: Optional[list[str]]) -> tuple[bool, str]:
    if expected_files is None:
        return True, ""
    extra = set(changed_files) - set(expected_files)
    if extra:
        return False, f"unexpected_files: {sorted(extra)} not in expected {expected_files}"
    return True, ""


def check_warnings(warnings: Optional[list[str]]) -> tuple[bool, str]:
    if warnings:
        return False, f"unresolved_warnings: {warnings}"
    return True, ""


def check_acceptance_machine_checkable(is_checkable: bool) -> tuple[bool, str]:
    if not is_checkable:
        return False, "acceptance: not machine-checkable"
    return True, ""


def evaluate(
    *,
    report_path: Optional[str | Path],
    changed_files: list[str],
    test_results: Optional[dict],
    warnings: Optional[list[str]] = None,
    expected_files: Optional[list[str]] = None,
    is_checkable: bool = True,
) -> GateResult:
    reasons: list[str] = []

    ok, msg = check_report_exists(report_path)
    if not ok:
        reasons.append(msg)

    ok, msg = check_tests_pass(test_results)
    if not ok:
        reasons.append(msg)

    ok, msg = check_files_changed(changed_files)
    if not ok:
        reasons.append(msg)

    ok, msg = check_expected_files_only(changed_files, expected_files)
    if not ok:
        reasons.append(msg)

    ok, msg = check_warnings(warnings)
    if not ok:
        reasons.append(msg)

    ok, msg = check_acceptance_machine_checkable(is_checkable)
    if not ok:
        reasons.append(msg)

    if reasons:
        return GateResult(verdict="escalate", reasons=tuple(reasons))
    return GateResult(verdict="auto_approve", reasons=())
