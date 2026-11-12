"""Cross-check roadmap definitions against persisted board state.

The task board has two halves that must describe exactly the same task IDs:

``definitions``
    ``docs/iteration improvement ideas.md`` plus every file under
    ``delegated_tasks/`` (parsed by :mod:`opencode_crack.orchestrator.task_parser`).
    A task ID is a definition only when it matches the parser's header
    pattern (``H/D/C-`` followed by exactly three digits), so legacy or
    incidental headings that the generator never counts are never reported
    here.
``state``
    ``orchestration/tasks.yaml`` — written only by
    :mod:`opencode_crack.orchestrator.task_board`.

``STATUS.md`` is rendered from the definitions, so any ID that exists in one
half and not the other makes the dashboard and the state file disagree. That
is exactly the divergence repaired on this branch: ``STATUS.md`` reported 482
tasks while ``tasks.yaml`` held 471 rows, because mutations persisted only the
rows they found and never materialized newly added definitions.

This module reports every violation of

    set(definition IDs) == set(state IDs)

plus the defects that would silently corrupt either half: duplicate
definitions across source documents, invalid statuses, tier
misclassification, dependency references that point at no definition, and
dependency cycles.

Reported kinds
--------------
``duplicate_definition``    the same task ID is defined more than once across
                            the source documents.
``tier_mismatch``           a delegated extension document defines a non-D
                            task, or a state row's explicit ``tier`` disagrees
                            with the definition.
``missing_state_row``       a definition has no ``tasks.yaml`` row, so
                            ``STATUS.md`` counts it but the state file does
                            not track it.
``orphaned_state_entry``    a ``tasks.yaml`` row has no definition, so it is
                            invisible to the generator and cannot be claimed
                            or rendered.
``invalid_status``          a row's status is missing or outside
                            ``task_board.VALID_STATUSES``.
``invalid_state_entry``     a row is not a mapping at all.
``invalid_state_root``      the persisted YAML document is not a mapping.
``invalid_state_key``       a persisted top-level task key is not a string.
``duplicate_state_entry``   a task ID occurs more than once in the YAML mapping.
``invalid_state_yaml``      the persisted YAML cannot be parsed or constructed.
``missing_dependency``      a task declares a dependency on an ID that no
                            document defines.
``circular_dependency``     a deterministic DFS cycle witness exists in the declared dependency graph.

Dependency detection recognizes explicit declaration lines only, not ordinary
prose. Forward declarations (``Depends on``, ``Depends-on``, ``Blocked by``,
``Requires``) add an edge from the current task to each referenced
prerequisite. Reverse declarations (``Required by``) add an edge from the
named dependent back to the current task. Both forms are checked for missing
task references. The declaration keyword must start the line (allowing
indentation and Markdown bold fencing) and must appear in the task's metadata
preamble, so titles and explanatory sentences that merely contain a
dependency-related word are not mistaken for declarations. Scanning every ID
token would report false positives such as D-040's deliberate
``task_parser.get_section_text("D-999")`` test example, which is prose about
an ID, not a dependency on one.

Read-only and deterministic: nothing here writes the roadmap, the board, or
git, and results are sorted by ``(subject, kind, detail)`` so repeated runs
and CI comparisons agree. It reports violations instead of raising, so the
caller decides how to act — this is a check, not a gate.
"""
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

import yaml
from yaml.nodes import MappingNode, ScalarNode

from opencode_crack.orchestrator import task_board, task_parser


# Same header definition task_parser uses to count board IDs: only these are
# "definitions" for the invariant, so validation must use exactly this shape.
_HEADER_RE = task_parser.HEADER_RE

# A line that merely mentions a dependency-related word in prose, a heading,
# or an "Out of scope" note must not be treated as a declaration. A declaration
# is anchored at the start of the line (allowing indentation and Markdown bold
# fencing) and uses an explicit keyword followed by a value:
#
#     **Depends on:** D-002
#     **Depends-on:** D-001 and C-003
#     Depends on D-004.
#     Required by D-005.
#
# Ordinary sentences such as "After claiming D-001, run the cycle" or "D-002
# requires review" are not declarations, even though they contain a
# dependency-related word, because the keyword is not a line-start field.
#
# Declarations are only honored from a task's metadata preamble: once a
# section passes a non-task ``##`` subheading, declaration-like lines after it
# belong to a nested or following task block (for example content bleeding in
# from an unrecognized ``## D-318-Round-3 ...`` heading) and must not be
# attributed to this task.
_DECLARE_RE = re.compile(
    r"^(?:\s*\*\*)?(Depends[- ]on|Blocked by|Requires|Required by)\b(?:\s*:|\s)",
    re.IGNORECASE,
)
_TASK_REF_RE = re.compile(r"\b([HDC]-\d{3})\b")


@dataclass(frozen=True)
class Issue:
    """One validated violation, addressed by kind + subject + detail.

    ``subject`` is the task ID the finding is about (or the cycle rendered as
    ``A -> B -> A`` for ``circular_dependency``), so a caller can group by
    task or filter by kind without parsing prose.
    """

    kind: str
    subject: str
    detail: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.subject}: {self.detail}"


def _scan_definitions(roadmap_path: Optional[Path]):
    """Yield ``(task_id, section_text, document_name, is_delegated)`` for
    every task-shaped header in every source document, in document order.

    Mirrors `task_parser` exactly (HEADER_RE over the main roadmap plus the
    delegated extension files), so definition IDs equal what the generator
    counts — never more, never less.
    """
    for document in task_parser.documents(roadmap_path):
        try:
            is_delegated = document.resolve().parent == task_parser.DELEGATED_TASKS_DIR.resolve()
        except OSError:  # pragma: no cover - resolve() failure is environmental
            is_delegated = False
        text = document.read_text(encoding="utf-8")
        headers = list(_HEADER_RE.finditer(text))
        for i, match in enumerate(headers):
            section_end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
            section = text[match.start():section_end].strip()
            yield match.group(1), section, document.name, is_delegated


def _declared_dependencies(
    sections: Mapping[str, str],
) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Return dependency graph edges and all declared task references.

    Only explicit dependency-declaration lines are honored: a line whose start
    (after optional indentation and Markdown bold fencing) is one of
    ``Depends on``, ``Depends-on``, ``Blocked by``, ``Requires`` or
    ``Required by`` followed by a value. Forward declarations add an edge from
    the current task to each referenced prerequisite. ``Required by`` is the
    inverse relation: the named dependent relies on the current task, so its
    graph edge points back to the current task.

    Declarations are only taken from a task's metadata preamble. Once a
    section passes a non-task ``##`` subheading, declaration-like lines after
    it belong to a nested or following task block and are not attributed to
    this task, which prevents content bleeding in from a neighboring block
    (for example an unrecognized ``## D-318-Round-3 ...`` heading) from being
    mistaken for the current task's own dependencies.

    Both forms are checked for missing task references.
    """
    edges: dict[str, set[str]] = {}
    references: dict[str, set[str]] = {}
    for task_id, section in sections.items():
        passed_preamble = False
        for line in section.splitlines():
            if (
                not passed_preamble
                and re.match(r"^##\s", line)
                and not _HEADER_RE.match(line)
            ):
                # A non-task ## subheading ends this task's metadata preamble.
                passed_preamble = True
            if passed_preamble:
                continue
            refs = set(_TASK_REF_RE.findall(line))
            if not refs:
                continue
            match = _DECLARE_RE.search(line)
            if not match:
                continue
            keyword = match.group(1).lower()
            if keyword == "required by":
                # Reverse declaration: the named dependent relies on this task.
                references.setdefault(task_id, set()).update(refs)
                for dependent_id in refs:
                    edges.setdefault(dependent_id, set()).add(task_id)
            else:
                # Forward declaration: this task relies on each prerequisite.
                references.setdefault(task_id, set()).update(refs)
                edges.setdefault(task_id, set()).update(refs)
    return edges, references


def _canonical_cycle(cycle: list[str]) -> tuple[str, ...]:
    """Rotate a cycle to start at its smallest ID so the same loop always
    renders (and deduplicates) identically."""
    body = cycle[:-1] if len(cycle) > 1 and cycle[0] == cycle[-1] else list(cycle)
    if not body:
        return ()
    start = body.index(min(body))
    return tuple(body[start:] + body[:start])


def _find_cycles(edges: Mapping[str, set[str]]) -> list[tuple[str, ...]]:
    """Return deterministic DFS cycle witnesses in canonical rotation.

    This is a cycle detector, not an exhaustive enumeration of every simple
    cycle. Each returned tuple is a real directed cycle with no repeated
    closing node; one witness is sufficient to establish a cyclic dependency
    component without the potentially exponential cost of enumerating all
    simple cycles in a dense graph.
    """
    found: set[tuple[str, ...]] = set()
    colour: dict[str, int] = {}  # 1 = on the current path, 2 = fully explored
    path: list[str] = []

    def visit(node: str) -> None:
        colour[node] = 1
        path.append(node)
        for nxt in sorted(edges.get(node, ())):
            if colour.get(nxt) == 1:
                found.add(_canonical_cycle(path[path.index(nxt):]))
            elif colour.get(nxt) is None:
                visit(nxt)
        path.pop()
        colour[node] = 2

    for node in sorted(edges):
        if colour.get(node) is None:
            visit(node)
    return sorted(found)


def _normalize_state_mapping(raw: Any) -> tuple[dict[str, Any], list[Issue]]:
    """Keep string task IDs and report malformed roots/keys instead of crashing."""
    if not isinstance(raw, Mapping):
        return {}, [Issue(
            "invalid_state_root",
            "<root>",
            f"state document must be a mapping, not {type(raw).__name__}",
        )]

    states: dict[str, Any] = {}
    issues: list[Issue] = []
    for key, value in raw.items():
        if not isinstance(key, str):
            issues.append(Issue(
                "invalid_state_key",
                repr(key),
                f"top-level task IDs must be strings, not {type(key).__name__}",
            ))
            continue
        states[key] = value
    return states, issues


def _load_state_mapping(
    board_path: Optional[Path],
) -> tuple[dict[str, Any], list[Issue]]:
    """Read board rows while preserving duplicate-key evidence from YAML nodes.

    Normal YAML mapping construction collapses duplicate keys. Inspect the
    composed node tree first, then construct a mapping containing only valid
    string task IDs so malformed keys cannot crash the validator.
    """
    path = Path(board_path) if board_path is not None else task_board.BOARD_PATH
    if not path.exists():
        return {}, []

    try:
        with path.open("r", encoding="utf-8") as handle:
            root = yaml.compose(handle, Loader=yaml.SafeLoader)
    except (OSError, yaml.YAMLError) as exc:
        return {}, [Issue(
            "invalid_state_yaml",
            "<root>",
            f"could not parse {path.name}: {type(exc).__name__}",
        )]

    if root is None:
        return {}, [Issue(
            "invalid_state_root",
            "<root>",
            f"{path.name} is empty and must contain a YAML mapping",
        )]
    if not isinstance(root, MappingNode) or root.tag != "tag:yaml.org,2002:map":
        return {}, [Issue(
            "invalid_state_root",
            "<root>",
            f"{path.name} must contain a YAML mapping, not {root.tag}",
        )]

    issues: list[Issue] = []
    seen: set[str] = set()
    valid_pairs = []
    for key_node, value_node in root.value:
        if (
            not isinstance(key_node, ScalarNode)
            or key_node.tag != "tag:yaml.org,2002:str"
        ):
            key_label = repr(key_node.value) if isinstance(key_node, ScalarNode) else type(key_node).__name__
            issues.append(Issue(
                "invalid_state_key",
                key_label,
                "top-level task IDs must be scalar YAML strings",
            ))
            continue

        task_id = key_node.value
        if task_id in seen:
            issues.append(Issue(
                "duplicate_state_entry",
                task_id,
                f"{path.name} defines this task ID more than once",
            ))
        seen.add(task_id)
        valid_pairs.append((key_node, value_node))

    filtered_root = MappingNode(root.tag, valid_pairs, flow_style=root.flow_style)
    loader = yaml.SafeLoader("")
    try:
        states = loader.construct_document(filtered_root)
    except (yaml.YAMLError, TypeError, ValueError) as exc:
        issues.append(Issue(
            "invalid_state_yaml",
            "<root>",
            f"could not construct {path.name}: {type(exc).__name__}",
        ))
        return {}, issues
    finally:
        loader.dispose()

    if not isinstance(states, dict):
        issues.append(Issue(
            "invalid_state_root",
            "<root>",
            f"{path.name} must contain a YAML mapping",
        ))
        return {}, issues
    return states, issues

def validate_board(
    states: Optional[Mapping] = None,
    roadmap_path: Optional[Path] = None,
    board_path: Optional[Path] = None,
) -> list[Issue]:
    """Return every definition/state invariant violation as an :class:`Issue`.

    ``states`` defaults to the rows in ``tasks.yaml``. The file reader checks
    raw YAML mapping nodes first so duplicate keys and malformed roots or
    top-level keys are reported instead of being silently collapsed. Explicit
    mappings supplied by callers use the same string-key validation.
    ``roadmap_path`` limits the definition scan to one document (fixture mode)
    instead of the main roadmap plus the delegated extension files.

    Returns ``[]`` only when the two halves define the same ID set and no
    other checked defect is present.
    """
    if states is None:
        states, state_issues = _load_state_mapping(board_path)
    else:
        states, state_issues = _normalize_state_mapping(states)
    board_name = Path(board_path).name if board_path is not None else task_board.BOARD_PATH.name

    issues: list[Issue] = list(state_issues)

    # --- definitions ---------------------------------------------------
    occurrences: dict[str, list[str]] = {}
    sections: dict[str, str] = {}
    tiers: dict[str, str] = {}
    definition_ids: set[str] = set()

    for task_id, section, document_name, is_delegated in _scan_definitions(roadmap_path):
        occurrences.setdefault(task_id, []).append(document_name)
        prefix = task_id.split("-", 1)[0]

        definition_ids.add(task_id)
        tiers.setdefault(task_id, task_parser.TIER_NAMES[prefix])
        sections[task_id] = section

        if is_delegated and prefix != "D":
            issues.append(Issue(
                "tier_mismatch",
                task_id,
                f"{document_name}: delegated extension documents must define "
                f"D-tier tasks, not {prefix!r}",
            ))

    for task_id in sorted(occurrences):
        documents = occurrences[task_id]
        if len(documents) > 1:
            locations = ", ".join(sorted(set(documents)))
            issues.append(Issue(
                "duplicate_definition",
                task_id,
                f"defined {len(documents)} times in {locations}",
            ))

    # --- state rows ----------------------------------------------------
    state_ids = set(states)
    for task_id in sorted(state_ids):
        entry = states[task_id]
        if not isinstance(entry, dict):
            issues.append(Issue(
                "invalid_state_entry",
                task_id,
                f"{board_name}: row is {type(entry).__name__}, expected a mapping",
            ))
            continue

        status = entry.get("status")
        if not isinstance(status, str) or status not in task_board.VALID_STATUSES:
            issues.append(Issue(
                "invalid_status",
                task_id,
                f"{board_name}: status {status!r} is not one of "
                f"{sorted(task_board.VALID_STATUSES)}",
            ))

        declared_tier = entry.get("tier")
        if declared_tier is not None and task_id in tiers and declared_tier != tiers[task_id]:
            issues.append(Issue(
                "tier_mismatch",
                task_id,
                f"{board_name}: row declares tier {declared_tier!r} but the "
                f"definition is {tiers[task_id]!r}",
            ))

    for task_id in sorted(definition_ids - state_ids):
        issues.append(Issue(
            "missing_state_row",
            task_id,
            f"defined in the roadmap but has no row in {board_name}",
        ))
    for task_id in sorted(state_ids - definition_ids):
        issues.append(Issue(
            "orphaned_state_entry",
            task_id,
            f"has a row in {board_name} but no roadmap document defines it",
        ))

    # --- dependencies --------------------------------------------------
    edges, declared_references = _declared_dependencies(sections)
    for task_id in sorted(declared_references):
        for ref in sorted(declared_references[task_id] - definition_ids):
            issues.append(Issue(
                "missing_dependency",
                task_id,
                f"declares a dependency relationship with {ref}, which no document defines",
            ))

    known_edges = {
        task_id: {ref for ref in refs if ref in definition_ids}
        for task_id, refs in edges.items()
    }
    for cycle in _find_cycles(known_edges):
        issues.append(Issue(
            "circular_dependency",
            cycle[0],
            " -> ".join((*cycle, cycle[0])),
        ))

    return sorted(issues, key=lambda issue: (issue.subject, issue.kind, issue.detail))


if __name__ == "__main__":
    findings = validate_board()
    if findings:
        for line in findings:
            print(line)
        raise SystemExit(1)
    print("Board validation passed: roadmap definitions and tasks.yaml rows match.")
