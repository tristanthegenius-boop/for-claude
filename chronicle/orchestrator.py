"""The autonomous chapter loop: §25 intake → draft → gates → review → revise → commit."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from . import prompts
from .gates import GateReport, run_all
from .gates.heat import check_book
from .llm import parse_json
from .project import Project
from .state import commit
from .textstats import words


class ReviewError(Exception):
    pass


class ChatModel(Protocol):
    def chat(self, model: str, system: str, user: str, **kw) -> str: ...


@dataclass
class ChapterResult:
    chapter: int
    status: str  # "committed" | "needs_author"
    attempts: int
    report: GateReport | None = None
    review: dict = field(default_factory=dict)
    words: int = 0


def clean_output(text: str) -> str:
    """Strip fences or preamble a model may add before the '# Chapter' header."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    i = t.find("# Chapter")
    return (t[i:] if i > 0 else t).strip() + "\n"


class Orchestrator:
    def __init__(self, project: Project, llm: ChatModel, say: Callable[[str], None] = print):
        self.pj = project
        self.llm = llm
        self.say = say
        self._system = None

    @property
    def system(self) -> str:
        if self._system is None:
            self._system = prompts.system_prompt(self.pj.engine_text(), self.pj.working_bible())
        return self._system

    def _chat(self, role: str, user: str, **kw) -> str:
        cfg = self.pj.config
        kw.setdefault("temperature", cfg["temperature"] if role == "draft" else 0.3)
        kw.setdefault("max_tokens", cfg["max_tokens"])
        if cfg.get("reasoning", {}).get(role):
            kw.setdefault("reasoning", cfg["reasoning"][role])
        return self.llm.chat(self.pj.model(role), self.system, user, **kw)

    def _prev_tail(self, ch: int) -> str | None:
        p = self.pj.chapter_path(ch - 1)
        if not p.exists():
            return None
        w = p.read_text(encoding="utf-8").split()
        return " ".join(w[-600:])

    def review(self, bible, state, ch, raw) -> tuple[dict, list[str]]:
        """Reviewer failures are retried here; they never count as prose failures."""
        rv, err = None, None
        for _ in range(3):
            txt = self._chat("review", prompts.review_prompt(bible, state, ch, raw), json_mode=True)
            try:
                rv = parse_json(txt)
                break
            except Exception as e:  # noqa: BLE001
                err = e
                self.pj.log_event({"chapter": ch, "review_unparseable": txt[:2000]})
        if rv is None:
            raise ReviewError(f"reviewer returned unparseable JSON 3 times: {err}")
        hard = []
        verdict = str(rv.get("bible_verdict", "")).lower()
        if verdict not in ("matches", "deviation"):
            hard.append(f"§25 bible verdict '{verdict or 'none'}': {rv.get('verdict_reason', '')}")
        if rv.get("content_care_ok") is False:
            hard.append("§2 content care: " + "; ".join(rv.get("content_care_issues") or ["violation"]))
        beat = bible.beat(ch) or {}
        if beat.get("uf") and not rv.get("ufs_credited"):
            hard.append(f"§23 UF_GATE: zero UF credited (planned {beat['uf']}); zombie scene, seat a UF")
        return rv, hard

    def write_chapter(self, ch: int, start_draft: str | None = None) -> ChapterResult:
        cfg = self.pj.config
        bible = self.pj.bible()  # §25 step 0, every pass
        state = self.pj.state()
        self.say(bible.intake_banner())
        beat = bible.beat(ch) or {}
        self.say(f"ch{ch} intent: {beat.get('intent', '(no beat)')[:160]}")

        if start_draft:
            raw = clean_output(start_draft)
        else:
            raw = clean_output(self._chat("draft", prompts.draft_prompt(bible, state, ch, self._prev_tail(ch))))
        craft_left = int(cfg.get("craft_passes", 0))
        rep, rv = None, {}
        for attempt in range(1, int(cfg["max_attempts"]) + 1):
            dp = self.pj.draft_path(ch, attempt)
            dp.parent.mkdir(parents=True, exist_ok=True)
            dp.write_text(raw, encoding="utf-8")
            rep = run_all(bible, state, ch, raw)
            failures = list(rep.hard)
            if not failures:
                rv, review_hard = self.review(bible, state, ch, raw)
                rep.hard += review_hard
                failures = review_hard
            self.pj.log_event({"chapter": ch, "attempt": attempt, "hard": rep.hard, "warn": rep.warn,
                               "craft_score": rv.get("craft_score")})
            self.say(rep.render(f"attempt {attempt}"))

            if not failures and craft_left > 0 and (rv.get("craft_score") or 10) < cfg["min_craft_score"]:
                craft_left -= 1
                failures = [f"craft: {f}" for f in rv.get("craft_fixes", [])][:8]
                self.say(f"craft score {rv.get('craft_score')} < {cfg['min_craft_score']}: polishing")
            if not failures:
                return self._commit(bible, state, ch, raw, rep, rv, attempt)
            if attempt < int(cfg["max_attempts"]):
                raw = clean_output(
                    self._chat("draft", prompts.revise_prompt(bible, state, ch, raw, failures, len(words(raw))))
                )
        self.say(f"ch{ch}: still failing after {cfg['max_attempts']} attempts. Latest draft: {dp}")
        return ChapterResult(ch, "needs_author", int(cfg["max_attempts"]), rep, rv, len(words(raw)))

    def _commit(self, bible, state, ch, raw, rep, rv, attempt) -> ChapterResult:
        tok = self.pj.record_pass(ch, raw, True)
        hdr = rep.meta.get("header", {})
        summary = self._chat("summary", prompts.summary_prompt(ch, raw), max_tokens=4000).strip()
        fired = sorted(set(rep.meta.get("locks_fired_open", []) + list(rv.get("reveals_fired") or [])))
        prof = commit(state, ch, raw, tok, title=hdr.get("title", ""), pov=hdr.get("pov", ""),
                      fired=fired, ufs=list(rv.get("ufs_credited") or []), summary=summary)
        cp = self.pj.chapter_path(ch)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(raw, encoding="utf-8")
        self.pj.save_state(state)
        if bible.heat_rows(ch):
            hd = self.pj.heat_delivery()
            hd["book"] = bible.active_book
            hd["delivered"][str(ch)] = bool(rep.meta.get("heat_delivered"))
            hd["log"].append({"chapter": ch, "delivered": hd["delivered"][str(ch)]})
            self.pj.save_heat_delivery(hd)
        if rv:
            self.pj.log_adherence(ch, rv)
        total = sum(r["words"] for r in state["rhythm_history"])
        self.say(f"COMMITTED ch{ch} '{prof['title']}' ({prof['words']}w), running total {total}w")
        return ChapterResult(ch, "committed", attempt, rep, rv, prof["words"])

    def run(self, start: int, end: int) -> list[ChapterResult]:
        out = []
        bps = set(int(b) for b in self.pj.config.get("breakpoints", []))
        for ch in range(start, end + 1):
            r = self.write_chapter(ch)
            out.append(r)
            if r.status != "committed":
                self.say(f"Stopped at ch{ch}: needs author. Edit the draft, then `chronicle check` + `chronicle commit`.")
                break
            if ch in bps and ch != end:
                self.say(f"Breakpoint after ch{ch}. Read it, then resume with `chronicle run --from {ch + 1}`.")
                break
        return out

    def book_done(self) -> list[str]:
        bible = self.pj.bible()
        st = self.pj.state()
        hard = []
        missing = [c for c in range(1, bible.chapter_count + 1) if c not in st["committed_chapters"]]
        if missing:
            hard.append(f"chapters not committed: {missing}")
        d = {int(k): v for k, v in self.pj.heat_delivery().get("delivered", {}).items()}
        hard += check_book(bible, d)
        total = sum(r["words"] for r in st["rhythm_history"])
        lo, hi = st["length"]["target_words_total"]
        if not lo <= total <= hi:
            hard.append(f"book length {total}w outside [{lo},{hi}]")
        return hard
