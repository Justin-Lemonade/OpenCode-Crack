"""End-to-end lifecycle tests for durable institutional knowledge (Layer 2).

Covers the full production loop with hermetic tmp trees (never the real
``knowledge/`` dir):

    CREATE -> VALIDATE -> STORE -> DISCOVER -> SEARCH -> RANK -> RETRIEVE
      -> INJECT -> USE -> VERIFY -> UPDATE / SUPERSEDE

Test map to the acceptance scenarios:
  A create, B discover, C search, D ranking, E verification policy,
  F supersession, G contract injection, H irrelevance exclusion,
  I context budget, J worker handoff, K writer/reader compatibility.
"""
import re

import pytest

from opencode_crack.orchestrator import durable_knowledge as dk

DATE = "20260908"

WORKTREE_BODY = """Zebra-striped worktree detection keeps concurrent agents isolated.

## What We Discovered

- git rev-parse --absolute-git-dir reveals worktree membership
- Never remove the main worktree during cleanup
- Check path existence before operating on worktree metadata

## Evidence

- opencode_crack/orchestrator/worktree_guard.py — isolation enforcement
- tests/test_worktree_guard.py — 17 tests
"""

COMMIT_BODY = """Commit quarantine prevents cross-task contamination.

## What We Discovered

- Commit only files owned by the current task
- Re-check git status immediately before every commit

## Evidence

- reports/D-262_report.md — adopted WIP instead of discarding it
"""


@pytest.fixture
def kdir(tmp_path, monkeypatch):
    d = tmp_path / "knowledge"
    monkeypatch.setattr(dk, "DURABLE_DIR", d)
    monkeypatch.setattr(dk, "SIDECAR_PATH", tmp_path / "orchestration" / "durable_knowledge.yaml")
    return d


def _seed(kdir):  # noqa: ARG001 - kdir documents hermeticity via fixture
    worktree = dk.file_entry(
        title="Git worktree isolation for concurrent agents",
        body=WORKTREE_BODY, knowledge_type="learning",
        tags=["worktree", "isolation", "concurrency"],
        keywords=["worktree", "isolation"],
        task_ids=["D-900"], files=["opencode_crack/orchestrator/worktree_guard.py"],
        date_str=DATE)
    commit = dk.file_entry(
        title="Commit isolation in shared working trees",
        body=COMMIT_BODY, knowledge_type="learning",
        tags=["git", "commit"], keywords=["commit", "isolation"],
        date_str=DATE)
    pottery = dk.file_entry(
        title="Glazing techniques for stoneware pottery",
        body="Stoneware glaze chemistry has nothing to do with orchestration.\n\n## Evidence\n\n- kiln-log-7 — cone 6 firing schedule",
        knowledge_type="learning", tags=["pottery", "ceramics"],
        keywords=["glaze", "kiln"], date_str=DATE)
    return worktree, commit, pottery


# --- A: CREATE ---------------------------------------------------------------

class TestCreate:
    def test_file_entry_writes_correct_location_and_name(self, kdir):
        entry = dk.file_entry(
            title="Git worktree isolation", body="Some finding.",
            knowledge_type="learning", tags=["worktree"], task_ids=["D-900"],
            files=["opencode_crack/orchestrator/worktree_guard.py"], date_str=DATE)
        assert entry.path is not None
        path = kdir / entry.path if not str(entry.path).startswith(str(kdir)) else entry.path
        import pathlib
        p = pathlib.Path(entry.path)
        assert p.parent.name == "learnings"
        assert p.name.startswith(entry.id)
        assert p.exists()

    def test_file_entry_front_matter_complete(self, kdir):
        entry = dk.file_entry(
            title="Some decision", body="We chose X over Y.",
            knowledge_type="decision", tags=["x"], keywords=["y"],
            task_ids=["D-901"], priority="high", confidence="high",
            agents=["worker-a"], date_str=DATE)
        data, _ = dk._split_front_matter(
            __import__("pathlib").Path(entry.path).read_text(encoding="utf-8"))
        assert data["id"] == entry.id
        assert re.match(r"^KNOW-20260908-\d{3}$", data["id"])
        assert data["type"] == "decision"
        assert data["title"] == "Some decision"
        assert data["status"] == "active"
        assert data["priority"] == "high"
        assert data["confidence"] == "high"
        assert data["tags"] == ["x"]
        assert data["task_ids"] == ["D-901"]
        assert data["agents"] == ["worker-a"]
        assert data["created_at"] and data["updated_at"]

    def test_ids_unique_across_types(self, kdir):
        a = dk.file_entry(title="T1", body="B1", knowledge_type="learning", date_str=DATE)
        b = dk.file_entry(title="T2", body="B2", knowledge_type="architecture", date_str=DATE)
        c = dk.file_entry(title="T3", body="B3", knowledge_type="decision", date_str=DATE)
        assert len({a.id, b.id, c.id}) == 3
        assert dk.duplicates() == {}

    def test_writer_rejects_bad_input(self, kdir):
        with pytest.raises(ValueError):
            dk.file_entry(title="  ", body="B", knowledge_type="learning", date_str=DATE)
        with pytest.raises(ValueError):
            dk.file_entry(title="T", body="  ", knowledge_type="learning", date_str=DATE)
        with pytest.raises(ValueError):
            dk.file_entry(title="T", body="B", knowledge_type="nope", date_str=DATE)


# --- B: DISCOVER ---------------------------------------------------------------

class TestDiscover:
    def test_seeded_entries_discoverable_from_disk(self, kdir):
        worktree, commit, pottery = _seed(kdir)
        found = {e.id for e in dk.discover()}
        assert {worktree.id, commit.id, pottery.id} <= found

    def test_invalid_files_skipped_not_fatal(self, kdir):
        _seed(kdir)
        bad = kdir / "learnings" / "KNOW-20260908-099-broken.md"
        bad.parent.mkdir(parents=True, exist_ok=True)
        bad.write_text("no front matter here\n", encoding="utf-8")
        ids = {e.id for e in dk.discover()}
        assert len(ids) == 3
        assert "KNOW-20260908-099" not in ids
        assert dk.validate_file(bad) != []

    def test_crlf_files_parse(self, kdir):
        _seed(kdir)
        p = kdir / "learnings" / "KNOW-20260908-050-crlf-probe.md"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(
            b"---\r\nid: KNOW-20260908-050\r\ntype: learning\r\ntitle: CRLF probe\r\n"
            b"created_at: 2026-09-08T00:00:00Z\r\nupdated_at: 2026-09-08T00:00:00Z\r\n"
            b"status: active\r\n---\r\n\r\nBody here.\r\n")
        assert dk.validate_file(p) == []
        assert dk.get_entry("KNOW-20260908-050") is not None


# --- C: SEARCH -----------------------------------------------------------------

class TestSearch:
    def test_keyword_search_finds_match(self, kdir):
        worktree, _, _ = _seed(kdir)
        results = dk.search_entries(keywords=["worktree"])
        assert [r["id"] for r in results] == [worktree.id]

    def test_tag_filter_is_hard(self, kdir):
        worktree, commit, _ = _seed(kdir)
        results = dk.search_entries(keywords=["isolation"], tags=["worktree"])
        assert [r["id"] for r in results] == [worktree.id]
        assert commit.id not in [r["id"] for r in results]

    def test_task_id_filter(self, kdir):
        worktree, _, _ = _seed(kdir)
        results = dk.search_entries(keywords=[], task_ids=["D-900"])
        assert [r["id"] for r in results] == [worktree.id]

    def test_file_filter(self, kdir):
        worktree, _, _ = _seed(kdir)
        results = dk.search_entries(
            keywords=[], files=["opencode_crack/orchestrator/worktree_guard.py"])
        assert [r["id"] for r in results] == [worktree.id]

    def test_type_filter(self, kdir):
        _seed(kdir)
        decision = dk.file_entry(
            title="Throttleback scheduler choice", body="We chose throttleback.",
            knowledge_type="decision", tags=["scheduler"], date_str=DATE)
        results = dk.search_entries(keywords=["throttleback"], knowledge_types=["decision"])
        assert [r["id"] for r in results] == [decision.id]

    def test_no_match_returns_empty_not_everything(self, kdir):
        _seed(kdir)
        assert dk.search_entries(keywords=["quasicrystal"]) == []


# --- D: RANKING ------------------------------------------------------------------

class TestRanking:
    def test_task_and_file_links_outrank_keyword_only(self, kdir):
        worktree, commit, _ = _seed(kdir)
        ranked = dk.retrieve_for_task(
            task_id="D-900", title="Fix worktree cleanup crash",
            text=("agents crash during worktree cleanup; see "
                  "opencode_crack/orchestrator/worktree_guard.py. Also follow commit quarantine."),
            file_refs=["opencode_crack/orchestrator/worktree_guard.py"], record=False)
        ids = [s.entry.id for s in ranked]
        assert ids[0] == worktree.id
        assert commit.id in ids[1:]

    def test_useful_count_breaks_ties(self, kdir):
        a = dk.file_entry(
            title="Cache invalidation policy", body="Invalidate caches on write, alpha variant.",
            knowledge_type="learning", tags=["cache"], date_str=DATE)
        b = dk.file_entry(
            title="Cache invalidation policy", body="Invalidate caches on write, beta variant.",
            knowledge_type="learning", tags=["cache"], date_str=DATE)
        first = dk.retrieve_for_task(
            task_id="D-950", title="Cache invalidation policy",
            text="caches go stale after writes", record=False)
        order_before = [s.entry.id for s in first]
        assert set(order_before[:2]) == {a.id, b.id}
        loser = order_before[1]
        dk.mark_entry_useful(loser)
        dk.mark_entry_useful(loser)
        after = dk.retrieve_for_task(
            task_id="D-950", title="Cache invalidation policy",
            text="caches go stale after writes", record=False)
        assert [s.entry.id for s in after][0] == loser


# --- E: VERIFICATION POLICY -------------------------------------------------------

class TestVerificationPolicy:
    def test_retracted_excluded_from_automatic_retrieval(self, kdir):
        worktree, _, _ = _seed(kdir)
        doomed = dk.file_entry(
            title="Worktree folklore", body="Worktree claims without evidence.",
            knowledge_type="learning", tags=["worktree"], date_str=DATE)
        dk.retract_entry(doomed.id, reason="anecdote, no evidence")
        ranked = dk.retrieve_for_task(
            task_id="D-900", title="worktree isolation",
            text="worktree", record=False)
        ids = [s.entry.id for s in ranked]
        assert doomed.id not in ids
        assert worktree.id in ids

    def test_resolved_blockers_stay_eligible(self, kdir):
        blocker = dk.file_entry(
            title="Worktree provisioner outage", body="Outage postmortem notes.",
            knowledge_type="blocker", tags=["worktree"], date_str=DATE)
        dk.resolve_entry(blocker.id, notes="provisioner fixed")
        ranked = dk.retrieve_for_task(
            task_id="D-900", title="worktree isolation",
            text="worktree", record=False)
        assert blocker.id in [s.entry.id for s in ranked]

    def test_explicit_search_can_opt_into_superseded(self, kdir):
        old = dk.file_entry(
            title="Throttleback v1 notes", body="throttleback early notes.",
            knowledge_type="learning", tags=["scheduler"], date_str=DATE)
        new = dk.file_entry(
            title="Throttleback v2 notes", body="throttleback revised notes.",
            knowledge_type="learning", tags=["scheduler"], date_str=DATE)
        dk.supersede_entry(old.id, new.id, notes="v2 replaces v1")
        default = dk.search_entries(keywords=["throttleback"])
        assert old.id not in [r["id"] for r in default]
        assert new.id in [r["id"] for r in default]
        with_old = dk.search_entries(keywords=["throttleback"], include_superseded=True)
        assert old.id in [r["id"] for r in with_old]


# --- F: SUPERSESSION ---------------------------------------------------------------

class TestSupersession:
    def test_normal_retrieval_prefers_successor(self, kdir):
        v1 = dk.file_entry(
            title="Throttleback limits v1", body="throttleback set to 10.",
            knowledge_type="decision", tags=["scheduler"], date_str=DATE)
        v2 = dk.file_entry(
            title="Throttleback limits v2", body="throttleback raised to 25.",
            knowledge_type="decision", tags=["scheduler"],
            supersedes=[v1.id], date_str=DATE)
        dk.supersede_entry(v1.id, v2.id, notes="limit raised after load test")
        ranked = dk.retrieve_for_task(
            task_id="D-960", title="throttleback scheduler limits",
            text="throttleback", record=False)
        ids = [s.entry.id for s in ranked]
        assert v2.id in ids
        assert v1.id not in ids
        assert dk.get_entry(v1.id).status == "superseded"
        assert dk.get_entry(v1.id).superseded_by == v2.id


# --- G/H/I: CONTRACT INJECTION -------------------------------------------------------

def _task(task_id="D-900", title="Fix worktree cleanup crash"):
    from opencode_crack.orchestrator.task_board import Task
    return Task(id=task_id, tier="delegate", title=title, priority="HIGH")


SECTION = """## D-900 — Fix worktree cleanup crash

**Priority: HIGH.**

Agents crash during worktree cleanup; see opencode_crack/orchestrator/worktree_guard.py
for the isolation enforcement. The worktree isolation learning applies.

Relevant files: opencode_crack/orchestrator/worktree_guard.py
"""


class TestContractInjection:
    def test_contract_contains_actual_knowledge_content(self, kdir, monkeypatch):
        worktree, _, _ = _seed(kdir)
        monkeypatch.setattr(
            "opencode_crack.orchestrator.task_parser.get_section_text",
            lambda _tid: SECTION)
        from opencode_crack.orchestrator.delegator import build_contract
        contract = build_contract(_task())
        assert "RELEVANT PROJECT KNOWLEDGE" in contract
        # Content, not just the ID:
        assert worktree.id in contract
        assert "Zebra-striped worktree detection" in contract
        assert "git rev-parse --absolute-git-dir reveals worktree membership" in contract
        assert "task D-900" in contract
        assert "opencode_crack/orchestrator/worktree_guard.py" in contract

    def test_compact_and_json_carry_same_knowledge(self, kdir, monkeypatch):
        worktree, _, _ = _seed(kdir)
        monkeypatch.setattr(
            "opencode_crack.orchestrator.task_parser.get_section_text",
            lambda _tid: SECTION)
        import json
        from opencode_crack.orchestrator.delegator import (
            build_compact_contract, build_compact_contract_json, build_contract)
        full = build_contract(_task())
        compact = build_compact_contract(_task())
        data = json.loads(build_compact_contract_json(_task()))
        assert worktree.id in compact
        assert any(card["id"] == worktree.id for card in data["relevant_knowledge"])
        card = next(c for c in data["relevant_knowledge"] if c["id"] == worktree.id)
        assert any("D-900" in reason for reason in card["why_relevant"])
        # Compact stays a strict subset of the full form (whitespace-insensitive:
        # the full contract wraps some template lines the compact form keeps whole).
        def _collapse(text):
            return " ".join(text.split())
        collapsed_full = _collapse(full)
        formatting = {"Tier:", "SCOPE", "TASK", "ACCEPTANCE CRITERIA", "DEPENDENCIES",
                      "OUT OF SCOPE", "STOP CONDITIONS", "RELEVANT PROJECT KNOWLEDGE",
                      "----------------------------", "----", "RELEVANT FILES"}
        for line in compact.splitlines():
            if not line.strip() or line.startswith("#") or any(
                    line.startswith(m) for m in formatting):
                continue
            if line.startswith("- C-"):
                continue
            assert _collapse(line) in collapsed_full, f"compact line not in full contract: {line!r}"

    def test_irrelevant_knowledge_not_injected(self, kdir, monkeypatch):
        _, _, pottery = _seed(kdir)
        monkeypatch.setattr(
            "opencode_crack.orchestrator.task_parser.get_section_text",
            lambda _tid: SECTION)
        from opencode_crack.orchestrator.delegator import build_contract
        contract = build_contract(_task())
        assert pottery.id not in contract
        assert "stoneware" not in contract.lower()

    def test_empty_knowledge_means_no_section(self, kdir, monkeypatch):
        monkeypatch.setattr(
            "opencode_crack.orchestrator.task_parser.get_section_text",
            lambda _tid: SECTION)
        from opencode_crack.orchestrator.delegator import build_contract
        assert "RELEVANT PROJECT KNOWLEDGE" not in build_contract(_task())

    def test_context_budget_respected(self, kdir, monkeypatch):
        for i in range(6):
            dk.file_entry(
                title=f"Worktree finding number {i}",
                body=("Worktree detail sentence about isolation. " * 200).strip(),
                knowledge_type="learning", tags=["worktree"],
                task_ids=["D-901"], date_str=DATE)
        monkeypatch.setattr(
            "opencode_crack.orchestrator.task_parser.get_section_text",
            lambda _tid: SECTION.replace("D-900", "D-901"))
        from opencode_crack.orchestrator.delegator import build_compact_contract, build_contract
        full = build_contract(_task(task_id="D-901"))
        block = full.split("RELEVANT PROJECT KNOWLEDGE")[1].split("TASK\n----")[0]
        assert len(block) <= 3000 + len("\n----------------------------\n\n")
        compact = build_compact_contract(_task(task_id="D-901"))
        cblock = compact.split("RELEVANT PROJECT KNOWLEDGE")[1]
        assert len(cblock) <= 1500 + len("\n----------------------------\n")


# --- J: WORKER HANDOFF ---------------------------------------------------------------

class TestWorkerHandoff:
    def test_worker_b_retrieves_worker_a_knowledge_from_disk_only(self, kdir):
        # Worker A: creates knowledge through the production writer, then
        # "disappears" — nothing but the files on disk is shared.
        created = dk.file_entry(
            title="Provisioner retry backoff",
            body="Retry provisioning with exponential backoff.\n\n## Evidence\n\n- worker-a log line 42",
            knowledge_type="learning", tags=["provisioner", "retry"],
            keywords=["backoff"], task_ids=["D-910"],
            files=["src/runtime/provisioner.py"], date_str=DATE)
        created_id = created.id
        del created

        # Worker B: fresh scope, no conversation history — only a task brief.
        fresh = dk.discover()
        assert created_id in {e.id for e in fresh}
        ranked = dk.retrieve_for_task(
            task_id="D-911",
            title="Provisioner keeps failing without backoff",
            text="the provisioner needs retry backoff; see src/runtime/provisioner.py",
            file_refs=["src/runtime/provisioner.py"], record=True)
        assert ranked and ranked[0].entry.id == created_id
        assert "backoff" in ranked[0].entry.body
        assert dk.get_counts(created_id)["retrieval_count"] == 1


# --- K: WRITER/READER COMPAT ---------------------------------------------------------------

class TestWriterReaderCompat:
    def test_production_writer_round_trips_through_production_reader(self, kdir):
        created = dk.file_entry(
            title="Round-trip probe", body="Every field must survive.",
            knowledge_type="architecture", tags=["probe"], keywords=["probe"],
            task_ids=["D-920"], files=["src/x.py"], related=[],
            priority="high", confidence="high", agents=["probe"],
            date_str=DATE)
        reread = dk.get_entry(created.id)
        assert reread is not None
        for f in ("id", "type", "title", "status", "priority", "confidence",
                  "tags", "keywords", "task_ids", "files", "agents"):
            assert getattr(reread, f) == getattr(created, f), f
        assert "Round-trip probe" in reread.body
        hits = dk.search_entries(keywords=["probe"])
        assert [h["id"] for h in hits] == [created.id]


# --- CLI LIFECYCLE ---------------------------------------------------------------------

class TestCliLifecycle:
    def _run(self, capsys, *argv):
        from opencode_crack.orchestrator.cli import run_orchestrate
        try:
            run_orchestrate(list(argv))
            return 0, capsys.readouterr().out
        except SystemExit as exc:
            return exc.code, capsys.readouterr().out

    def test_durable_file_list_show_useful(self, kdir, capsys):
        code, out = self._run(
            capsys, "knowledge", "durable-file", "--type", "learning",
            "--title", "CLI filed finding", "--body", "Found via CLI.",
            "--tags", "cli,probe", "--task", "D-930")
        assert code == 0
        m = re.search(r"KNOW-\d{8}-\d{3}", out)
        assert m, out
        kid = m.group(0)

        code, out = self._run(capsys, "knowledge", "durable-list", "--tag", "cli")
        assert code == 0 and kid in out

        code, out = self._run(capsys, "knowledge", "durable-show", kid)
        assert code == 0 and "Found via CLI" in out

        code, out = self._run(capsys, "knowledge", "durable-useful", kid)
        assert code == 0 and "useful=1" in out

    def test_durable_supersede_and_retract(self, kdir, capsys):
        old = dk.file_entry(title="Old throttle", body="throttle old.",
                            knowledge_type="learning", tags=["t"], date_str=DATE)
        new = dk.file_entry(title="New throttle", body="throttle new.",
                            knowledge_type="learning", tags=["t"], date_str=DATE)
        code, out = self._run(
            capsys, "knowledge", "durable-supersede", old.id, "--with", new.id)
        assert code == 0 and "superseded by" in out
        assert dk.get_entry(old.id).status == "superseded"

        code, _ = self._run(
            capsys, "knowledge", "durable-retract", new.id, "--notes", "wrong")
        assert code == 0
        assert dk.get_entry(new.id).status == "retracted"

    def test_durable_file_rejects_bad_type(self, kdir, capsys):
        code, _ = self._run(
            capsys, "knowledge", "durable-file", "--type", "nope",
            "--title", "T", "--body", "B")
        assert code != 0


# --- REGRESSION: matching + transition-root robustness ---------------------------

class TestMatchingAndRoots:
    def test_file_affix_requires_path_boundary(self):
        assert dk._file_hit("x.py", ["complex.py"]) is None
        assert dk._file_hit("orchestrator/worktree_guard.py",
                            ["src/orchestrator/worktree_guard.py"]) == \
            "src/orchestrator/worktree_guard.py"
        assert dk._file_hit("src/orchestrator/worktree_guard.py",
                            ["worktree_guard.py"]) == "worktree_guard.py"

    def test_transitions_resolve_custom_named_trees(self, tmp_path, monkeypatch):
        """Supersede/retract/resolve must re-resolve inside the caller's
        tree even when it is not named "knowledge" (no drive-wide scan)."""
        base = tmp_path / "custom_tree"
        sc = tmp_path / "orchestration" / "durable_knowledge.yaml"
        old = dk.file_entry(title="Old way", body="Old body.",
                            knowledge_type="learning", tags=["t"],
                            date_str=DATE, directory=base)
        new = dk.file_entry(title="New way", body="New body.",
                            knowledge_type="learning", tags=["t"],
                            date_str=DATE, directory=base)
        assert old.path.startswith(str(base))
        done = dk.supersede_entry(old.id, new.id, notes="n",
                                  directory=base, sidecar_path=sc)
        assert done.status == "superseded"
        assert dk.get_entry(old.id, base).superseded_by == new.id
        resolved = dk.resolve_entry(new.id, notes="ok",
                                    directory=base, sidecar_path=sc)
        assert resolved.status == "resolved"
        retracted = dk.retract_entry(new.id, reason="actually wrong",
                                     directory=base, sidecar_path=sc)
        assert retracted.status == "retracted"
        assert dk.get_counts(new.id, sc)["retrieval_count"] == 0

    def test_oneliner_capped_for_budget_safety(self, kdir):
        entry = dk.file_entry(
            title="W" * 300, body="Body text here.",
            knowledge_type="learning", tags=["t"], date_str=DATE)
        scored = dk.retrieve_for_task(
            task_id="D-900", title="W" * 300, text="", record=False)
        assert scored and scored[0].entry.id == entry.id
        oneliner = dk.render_entry_lines(scored[0])[0]
        assert len(oneliner) <= 400 + 3  # cap + word-boundary ellipsis
