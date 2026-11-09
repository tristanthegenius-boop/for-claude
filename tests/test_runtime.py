import copy
import json
from pathlib import Path

import pytest

from chronicle.bible import Bible, IntakeError, intake, lane_guard, lint_probes
from chronicle.gates import run_all
from chronicle.gates.heat import check_chapter
from chronicle.gates.locks import scan
from chronicle.gates.validate import validate
from chronicle.orchestrator import Orchestrator
from chronicle.project import Project
from chronicle.state import CommitRefused, commit, file_hash, init_state
from chronicle.textstats import dialogue_report

EXAMPLE = Path(__file__).parent.parent / "examples" / "series_bible.example.json"


def bible_data():
    return json.loads(EXAMPLE.read_text())


def make_bible(d=None) -> Bible:
    return Bible(d or bible_data(), "test")


def sentence_block(n_words: int) -> str:
    """Varied, gate-friendly prose of roughly n_words."""
    pool = [
        "I walked down to the harbor before the light came up.",
        "The water was flat and grey.",
        "Was anyone watching the tower, or had they all gone home?",
        "My aunt had left the key under the third stone, the way she always had, and I found it by touch.",
        "Nothing moved.",
        "The lamp room smelled of oil and old paper, and somebody had swept it recently.",
        "I counted the steps on the way up because counting kept my hands steady.",
        "Outside, a gull turned once over the breakwater and was gone.",
    ]
    out, i = [], 0
    while sum(len(s.split()) for s in out) < n_words:
        out.append(pool[i % len(pool)])
        i += 1
        if i % 6 == 0:
            out.append("\n\n")
    return " ".join(out)


def chapter(ch: int, n_words: int = 3000, extra: str = "") -> str:
    return f"# Chapter {ch}\n### *The Light*\n**POV: Wren**\n\n{sentence_block(n_words)}\n\n{extra}\n"


# ── §25 intake ───────────────────────────────────────────────────────────
def test_intake_hard_stops(tmp_path):
    with pytest.raises(IntakeError):
        intake(tmp_path / "missing.json")
    d = bible_data()
    del d["books"]["1"]["chapter_spine_beatmap"]
    p = tmp_path / "b.json"
    p.write_text(json.dumps(d))
    with pytest.raises(IntakeError, match="chapter_spine_beatmap"):
        intake(p)


def test_lane_guard():
    b = make_bible()
    lane_guard(b, "cozy mystery")  # normalised match
    with pytest.raises(IntakeError, match="KERNEL_LANE_MISMATCH"):
        lane_guard(b, "MM Mafia Omegaverse")


# ── D1: cross-book lock scan exists and blocks early reveals ─────────────
def test_lock_scan_blocks_future_book_reveal():
    b = make_bible()
    rep = scan(b, 3, chapter(3, 200, "I knew then the keeper is alive."))
    assert rep.hard and "L1_keeper_alive" in rep.hard[0]
    b2 = b.with_active_book(2)
    assert not scan(b2, 10, chapter(10, 200, "The keeper is alive.")).hard
    assert scan(b2, 9, chapter(9, 200, "The keeper is alive.")).hard


# ── D2: generic probes are flagged ──────────────────────────────────────
def test_lint_flags_generic_probe():
    d = bible_data()
    d["series"]["reveal_schedule"]["locks"].append(
        {"id": "L9", "reveal": "x", "earliest_book": 2, "earliest_chapter": 1, "probe": "the ledger"}
    )
    issues = lint_probes(make_bible(d), {"bk1.md": "She opened the ledger again."})
    assert any("generic" in i and "L9" in i for i in issues)
    assert any("already occurs" in i for i in issues)
    assert not any("L1_keeper_alive" in i and "generic" in i for i in issues)


# ── D3: dialogue ratio is paragraph-scoped ──────────────────────────────
def test_missing_close_quote_is_caught():
    txt = '"Come here," he said.\n\n"Wait, I said.\n\nThe room was long and very quiet and full of dust.'
    assert dialogue_report(txt).errors


def test_multi_paragraph_speech_is_legal():
    txt = '"First paragraph of a speech.\n\n"Second paragraph, closed."\n\nNarration follows here.'
    r = dialogue_report(txt)
    assert not r.errors
    assert 0 < r.ratio_pct < 100


def test_curly_quotes():
    assert not dialogue_report("“Hello,” she said. It was late.").errors


# ── D4: heat gate false positives ───────────────────────────────────────
def heat_bible():
    d = bible_data()
    d["books"]["1"]["heat_delivery"] = [
        {"scene_id": "HD-1", "chapter": 5, "target_words": 900, "bite_beat": "none"}
    ]
    return make_bible(d)


def heat_scene(n=1000):
    unit = "His mouth found my skin and my breath caught; I said yes and meant it, and his hands were slow. "
    body = unit * (n // len(unit.split()) + 1)
    after = "Afterward we lay in the warm dark and talked about nothing, and I let myself be held. " * 15
    return body + "\n\nHe said my name and the bond settled between us like a held note.\n\n" + after


def test_scene_break_elsewhere_is_not_a_cutaway():
    raw = "# Chapter 5\n### *T*\n**POV: Wren**\n\nThe dock was busy at noon.\n\n---\n\n" + heat_scene()
    rep = check_chapter(heat_bible(), 5, raw)
    assert not [h for h in rep.hard if h.startswith("H1")], rep.hard


def test_retrospective_by_the_time_is_not_a_cutaway():
    raw = "# Chapter 5\n### *T*\n**POV: Wren**\n\n" + heat_scene().replace(
        "I said yes", "by the time I said yes I was sure"
    )
    assert not [h for h in check_chapter(heat_bible(), 5, raw).hard if h.startswith("H1")]


def test_real_time_skip_inside_scene_fails():
    s = heat_scene()
    mid = len(s) // 3
    raw = "# Chapter 5\n### *T*\n**POV: Wren**\n\n" + s[:mid] + " Hours later, his hands " + s[mid:]
    assert any(h.startswith("H1") for h in check_chapter(heat_bible(), 5, raw).hard)


def test_unwritten_heat_scene_fails():
    assert any(h.startswith("H4") for h in check_chapter(heat_bible(), 5, "# Chapter 5\n\nThe sea.").hard)


# ── D5: state template calibration ──────────────────────────────────────
def test_init_state_is_calibrated():
    st = init_state(make_bible())
    assert st["reveal_ledger"]["spent_probes"] == {}
    assert st["length"]["chapter_band"] == [2850, 3300]  # 60k / 20 chapters
    assert st["voice_bands"]["ellipsis_per1k"] == [0.4, 1.4]


def test_string_spent_probes_does_not_fail_every_chapter():
    st = init_state(make_bible())
    st["reveal_ledger"]["spent_probes"] = "the keeper"
    rep = validate(st, 3, chapter(3))
    assert not [h for h in rep.hard if h.startswith("reveal")]


# ── §24 validate + commit discipline ────────────────────────────────────
def test_validate_hard_rules():
    st = init_state(make_bible())
    rep = validate(st, 3, chapter(3).replace("flat and grey.", "flat — and grey."))
    assert any("em-dash" in h for h in rep.hard)
    assert any("word count" in h for h in validate(st, 3, chapter(3, 500)).hard)
    assert any("header" in h for h in validate(st, 4, chapter(3)).hard)
    assert validate(st, 3, chapter(3)).passed


def test_commit_refuses_without_pass_token():
    st = init_state(make_bible())
    raw = chapter(3)
    with pytest.raises(CommitRefused):
        commit(st, 3, raw, None)
    with pytest.raises(CommitRefused):  # token for different text
        commit(st, 3, raw, {"chapter": 3, "hash": file_hash(raw + "x"), "passed": True})
    with pytest.raises(CommitRefused):  # failing token
        commit(st, 3, raw, {"chapter": 3, "hash": file_hash(raw), "passed": False})
    commit(st, 3, raw, {"chapter": 3, "hash": file_hash(raw), "passed": True}, summary="s")
    assert st["committed_chapters"] == [3] and st["chapter_summaries"]["3"] == "s"


# ── orchestrator end to end with a fake model ───────────────────────────
class FakeLLM:
    """First draft is too short; revision passes; reviewer approves."""

    def __init__(self):
        self.calls = []

    def chat(self, model, system, user, **kw):
        self.calls.append(user[:40])
        assert "RUNTIME NOTICE" in system
        if "acquisitions editor" in user:
            return json.dumps({"bible_verdict": "matches", "verdict_reason": "ok", "content_care_ok": True,
                               "ufs_credited": ["UF-01"], "reveals_fired": [], "craft_score": 9, "craft_fixes": []})
        if user.startswith("Summarize"):
            return "Wren arrives and finds the light burning."
        ch = int(user.split("Write Chapter ")[1].split()[0]) if "Write Chapter" in user else 1
        if "rejected this draft" in user:
            return "Here you go:\n" + chapter(ch)
        return chapter(ch, 400)


def test_orchestrator_revises_then_commits(tmp_path):
    (tmp_path / "engine.txt").write_text("ENGINE SPEC\n[BEGIN validate.py]\ncode\n[END validate.py]\n")
    (tmp_path / "series_bible.json").write_text(EXAMPLE.read_text())
    pj = Project(tmp_path)
    cfg = json.loads(json.dumps(__import__("chronicle.project", fromlist=["x"]).DEFAULT_CONFIG))
    cfg["models"] = {"draft": "test/model"}
    pj.save_config(cfg)
    pj.save_state(init_state(pj.bible()))
    llm = FakeLLM()
    orch = Orchestrator(pj, llm, say=lambda s: None)
    assert "executed by the Chronicle Runtime" in orch.system and "code" not in orch.system.split("SPEC")[1]
    r = orch.write_chapter(1)
    assert r.status == "committed" and r.attempts == 2
    st = pj.state()
    assert st["committed_chapters"] == [1]
    assert pj.chapter_path(1).read_text().startswith("# Chapter 1")
    assert st["chapter_summaries"]["1"].startswith("Wren")
    assert json.loads(pj.p("bible_adherence.json").read_text())["1"]["bible_verdict"] == "matches"


def test_drift_verdict_blocks_commit(tmp_path):
    class Drifter(FakeLLM):
        def chat(self, model, system, user, **kw):
            if "acquisitions editor" in user:
                return json.dumps({"bible_verdict": "drift", "verdict_reason": "off spine",
                                   "content_care_ok": True, "ufs_credited": ["UF-01"]})
            return chapter(1)

    (tmp_path / "engine.txt").write_text("ENGINE")
    (tmp_path / "series_bible.json").write_text(EXAMPLE.read_text())
    pj = Project(tmp_path)
    pj.save_config({"models": {"draft": "m"}, "max_attempts": 2, "craft_passes": 0})
    pj.save_state(init_state(pj.bible()))
    r = Orchestrator(pj, Drifter(), say=lambda s: None).write_chapter(1)
    assert r.status == "needs_author"
    assert pj.state()["committed_chapters"] == []


def test_bad_review_json_is_retried_not_treated_as_prose_failure(tmp_path):
    class FlakyReviewer(FakeLLM):
        n = 0

        def chat(self, model, system, user, **kw):
            if "acquisitions editor" in user:
                FlakyReviewer.n += 1
                if FlakyReviewer.n == 1:
                    return "not json"
            return super().chat(model, system, user, **kw)

    (tmp_path / "engine.txt").write_text("ENGINE")
    (tmp_path / "series_bible.json").write_text(EXAMPLE.read_text())
    pj = Project(tmp_path)
    pj.save_config({"models": {"draft": "m"}})
    pj.save_state(init_state(pj.bible()))
    llm = FlakyReviewer()
    r = Orchestrator(pj, llm, say=lambda s: None).write_chapter(1, start_draft=chapter(1))
    assert r.status == "committed" and r.attempts == 1
    assert not any("rejected this draft" in c for c in llm.calls)
