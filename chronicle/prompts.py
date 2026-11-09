"""Prompt assembly. The engine spec is the system prompt; the runtime supplies the
live plan (bible), live state (state.json) and gate results each call."""
from __future__ import annotations

import json
import re

from .bible import Bible
from .state import chapter_band, effective_bands

HEADLESS = """\
RUNTIME NOTICE (outranks the engine's session-opening instructions, nothing else):
You are running HEADLESS inside the Chronicle Runtime, software that executes the
Chronicle Engine specification below. Do not greet, do not act as Iris, do not ask
questions, do not narrate gate results. The runtime runs the §24/§25/§26 gates as
real code after you write; never print fake validate/commit blocks. Output exactly
what each request asks for and nothing else.
"""

CODE_BLOCK = re.compile(r"\[BEGIN ([^\]]+)\].*?\[END \1\]", re.S)


def engine_for_prompt(engine_text: str) -> str:
    """Drop the embedded gate scripts; the runtime executes them, the model needn't read them."""
    return CODE_BLOCK.sub(lambda m: f"[{m.group(1)}: executed by the Chronicle Runtime]", engine_text)


def system_prompt(engine_text: str, working_bible: str | None = None) -> str:
    parts = [HEADLESS, "=" * 79, engine_for_prompt(engine_text)]
    if working_bible:
        parts += ["=" * 79, "ACTIVE BOOK WORKING BIBLE (prose plan from the codex):", working_bible]
    return "\n".join(parts)


def _j(o) -> str:
    return json.dumps(o, indent=1, ensure_ascii=False)


def plan_context(bible: Bible, state: dict, ch: int) -> str:
    beat = bible.beat(ch) or {}
    rows = bible.heat_rows(ch)
    lo, hi = chapter_band(state, ch)
    book = bible.book
    forbidden = [l["probe"] for l in bible.locked_probes(ch)]
    summaries = state.get("chapter_summaries", {})
    prior = "\n".join(f"Ch {k}: {v}" for k, v in sorted(summaries.items(), key=lambda kv: int(kv[0])) if int(k) < ch)
    motifs = {
        k: {"cap": m.get("cap"), "used": len(m.get("used_in_chapters", []))}
        for k, m in (state.get("motifs") or {}).items()
        if not k.startswith("_")
    }
    crutches = [
        p for p, i in state.get("phrase_watch", {}).get("phrases", {}).items() if len(i.get("chapters", [])) >= 2
    ]
    s = [
        f"SERIES: {bible.data['series'].get('title', '')} | LANE: {bible.lane} | BOOK {bible.active_book}: {book.get('title', '')}",
        f"BOOK JOB: {book.get('book_job', '')}",
        f"FOCAL: {_j(book.get('focal', ''))}",
        f"POV/TENSE: {book.get('pov_tense', '')}",
        f"CONTENT CARE (tier 1, never relaxed):\n{_j(bible.content_care)}",
        f"ALREADY KNOWN CANON (reference, never re-stage):\n{_j(bible.already_known)}",
        f"CHAPTER {ch} of {bible.chapter_count} BEAT INTENT:\n{_j(beat)}",
        f"WORD BAND: {lo}-{hi} words of prose (aim for {round((lo + hi) / 2)}).",
        f"VOICE BANDS (measured after you write): {_j(effective_bands(state, ch, None))}",
        "HARD RULES: no em-dashes and no double hyphens anywhere; past tense; close every quotation "
        "(multi-paragraph speech: open each paragraph, close at the end only).",
        "FORBIDDEN STRINGS this chapter (series reveal locks not yet open; do not write these words in "
        f"this order even innocently): {_j(forbidden)}",
    ]
    if rows:
        s.append(
            "DESIGNATED HEAT CHAPTER. Scene contract §26.2 P1-P4 applies to this row; write the scene "
            "continuous with no scene break, time skip or fade inside it, and land a real aftermath of "
            f"200+ words after the final bond beat:\n{_j(rows)}"
        )
    if motifs:
        s.append(f"MOTIF BUDGET (cap / used so far): {_j(motifs)}")
    if crutches:
        s.append(f"DO NOT REACH FOR (already used repeatedly): {_j(crutches)}")
    if prior:
        s.append(f"STORY SO FAR (committed chapters):\n{prior}")
    return "\n\n".join(s)


def draft_prompt(bible: Bible, state: dict, ch: int, prev_tail: str | None) -> str:
    tail = f"\n\nLAST 600 WORDS OF CHAPTER {ch - 1} (continue from here):\n{prev_tail}" if prev_tail else ""
    return (
        f"{plan_context(bible, state, ch)}{tail}\n\n"
        f"TASK: Write Chapter {ch} in full, as publishable prose under every craft rule in the engine "
        "(MODE_TRISTAN voice, ANTI_SLOP patterns 1-13, §21 rhythm, §22 priming, §23 UF, DDL dialogue, "
        "CML compulsion, EPL). Serve the beat intent; earn every scene its exit state.\n"
        "Output ONLY the chapter, starting with exactly these header lines:\n"
        f"# Chapter {ch}\n### *<Title>*\n**POV: <Name>**\n"
    )


def revise_prompt(bible: Bible, state: dict, ch: int, draft: str, failures: list[str], wc: int) -> str:
    lo, hi = chapter_band(state, ch)
    length = ""
    if wc < lo:
        length = (
            f"\nLENGTH: the draft is {wc} words; it must reach {lo}-{hi}. Expand with interiority, "
            "reported speech and sensory grounding inside existing scenes; do not pad with new subplots."
        )
    elif wc > hi:
        length = f"\nLENGTH: the draft is {wc} words; cut to {lo}-{hi} by compressing, not by deleting beats."
    fl = "\n".join(f"- {f}" for f in failures)
    return (
        f"{plan_context(bible, state, ch)}\n\n"
        f"The runtime gates rejected this draft of Chapter {ch}. Fix every failure surgically "
        "(engine SF libraries), keep everything that works, and return the COMPLETE revised chapter "
        f"with the same header format. Output only the chapter.\n\nFAILURES:\n{fl}{length}\n\n"
        f"DRAFT:\n{draft}"
    )


REVIEW_SCHEMA = """{
 "bible_verdict": "matches" | "deviation" | "drift",
 "verdict_reason": "<one sentence: how the chapter serves (or departs from) the beat intent>",
 "content_care_ok": true | false,
 "content_care_issues": ["..."],
 "ufs_credited": ["UF-NN", ...],
 "reveals_fired": ["<lock id>", ...],
 "epl": {"EJ-1": "...", "EJ-2": "...", "EJ-3": "...", "EJ-4": "...", "EJ-5": "...", "EJ-6": "..."},
 "craft_score": <1-10, bestseller-shelf standard>,
 "craft_fixes": ["<specific, located, actionable fix>", ...]
}"""


def review_prompt(bible: Bible, state: dict, ch: int, chapter: str) -> str:
    return (
        f"{plan_context(bible, state, ch)}\n\n"
        "TASK: You are the engine's audit layer (§25.3 bible verdict, §2 tier-1 content care, §23 "
        "UF_GATE, EPL six questions) acting as a demanding acquisitions editor. Judge the chapter "
        "below against the plan above and the engine's craft standard. 'drift' means the chapter "
        "fails its beat intent; 'deviation' means it departs deliberately and defensibly. "
        "ufs_credited lists only UFs the scene genuinely delivers. craft_fixes are the highest-"
        "leverage line- and scene-level fixes (max 8), each pointing at a specific passage. "
        f"Return ONLY JSON matching:\n{REVIEW_SCHEMA}\n\nCHAPTER:\n{chapter}"
    )


def summary_prompt(ch: int, chapter: str) -> str:
    return (
        f"Summarize Chapter {ch} below for a continuity ledger in at most 140 words: what happened, "
        "what changed between the leads (trust, power, bond), any new named facts (names, numbers, "
        "dates, places), and the open question it ends on. Plain prose, no preamble.\n\n" + chapter
    )
