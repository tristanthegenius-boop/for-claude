"""§26 HEAT_DELIVERY_GATE (structural only), with kit D4 fixes.

D4 fixes:
 - the qualifying span is located INSIDE one scene (the densest heat-lexicon scene),
   so a scene break elsewhere in the chapter is no longer a false cutaway;
 - `by the time (I|we)` is removed from the cutaway list (ordinary retrospective
   narration in first-person past).
A scene break inside the qualifying scene still cannot occur, because scenes are
split at breaks: a scene cut in half shows up as a short span (H2) or a missing
aftermath (H3) instead.
"""
from __future__ import annotations

import re

from ..bible import Bible
from ..textstats import scenes
from . import GateReport

WORD_FLOOR = 900
AFTERMATH_FLOOR = 120
AFTERMATH_SOFT = 200
BUDGET_MIN, BUDGET_MAX = 3, 5

HEAT_LEX = re.compile(
    r"\b(heat|rut|scent|slick|knot|claim|bite|bond|nest|mark|mouth|skin|bare|undress|hands|thigh|hips|breath|moan|shiver|arous\w*)\b",
    re.I,
)
BOND_LEX = re.compile(r"\b(knot\w*|bite|bit down|claim(ed)?|bond(ed)?|mark(ed)?|mating)\b", re.I)
CUTAWAY = [
    re.compile(
        r"\b(later that (night|evening|morning)|the next (morning|day)|when I woke|hours later|sometime after)\b",
        re.I,
    ),
    re.compile(r"\b(everything went (dark|white|quiet)|and then I was gone|the world fell away and)\b", re.I),
]


def _wc(s: str) -> int:
    return len(s.split())


def qualifying_scene(raw: str) -> str | None:
    best, best_n = None, 0
    for sc in scenes(raw):
        n = len(HEAT_LEX.findall(sc))
        if n > best_n:
            best, best_n = sc, n
    return best


def check_chapter(bible: Bible, ch: int, raw: str) -> GateReport:
    rep = GateReport(ch)
    rows = bible.heat_rows(ch)
    if not rows:
        return rep
    sc = qualifying_scene(raw)
    if not sc:
        rep.hard.append(f"H4 ch{ch}: scheduled heat scene not written (no heat lexicon present)")
        rep.meta["heat_delivered"] = False
        return rep
    hits = [m.start() for m in HEAT_LEX.finditer(sc)]
    body = sc[hits[0] : hits[-1]]
    for pat in CUTAWAY:
        m = pat.search(body)
        if m:
            rep.hard.append(f"H1 ch{ch}: cutaway '{m.group(0)}' inside the qualifying span (P1 fails)")
    floor = max(WORD_FLOOR, min(int(r.get("target_words", WORD_FLOOR)) for r in rows))
    if _wc(body) < WORD_FLOOR:
        rep.hard.append(f"H2 ch{ch}: qualifying span {_wc(body)}w, hard floor {WORD_FLOOR}w")
    elif _wc(body) < floor:
        rep.warn.append(f"H2 ch{ch}: span {_wc(body)}w against target {floor}w (thin)")
    bonds = [m.end() for m in BOND_LEX.finditer(sc)]
    if bonds:
        tail = _wc(sc[bonds[-1] :])
        if tail < AFTERMATH_FLOOR:
            rep.hard.append(f"H3 ch{ch}: aftermath {tail}w after final bond beat, floor {AFTERMATH_FLOOR}w (P3 fails)")
        elif tail < AFTERMATH_SOFT:
            rep.warn.append(f"H3 ch{ch}: aftermath {tail}w (short landing)")
    if not any(h.startswith(("H1", "H2", "H3", "H4")) for h in rep.hard):
        rep.ok.append(f"heat: ch{ch} qualifying span {_wc(body)}w, structure clear ({rows[0].get('scene_id')})")
    rep.meta["heat_delivered"] = not rep.hard
    return rep


def check_book(bible: Bible, delivered: dict[int, bool]) -> list[str]:
    rows = bible.heat_rows()
    if not rows:
        return []
    hard = []
    n = sum(1 for r in rows if delivered.get(r.get("chapter")))
    if n < BUDGET_MIN:
        hard.append(f"B1 under budget: {n} qualifying scenes, floor {BUDGET_MIN}")
    if n > BUDGET_MAX:
        hard.append(f"B1 over budget: {n} qualifying scenes, ceiling {BUDGET_MAX}")
    for r in rows:
        if not delivered.get(r.get("chapter")):
            hard.append(f"B2 row {r.get('scene_id')} scheduled at ch {r.get('chapter')} never shipped")
    chs = sorted(r["chapter"] for r in rows if delivered.get(r.get("chapter")))
    mid = max(1, bible.chapter_count // 2)
    if chs and not any(c < mid for c in chs):
        hard.append(f"B4 no qualifying scene before the midpoint (ch {mid})")
    if not any(r.get("bite_beat") not in (None, "none") for r in rows):
        hard.append("B4 no qualifying scene carries a bite-ladder advance")
    for a, b in zip(chs, chs[1:]):
        if b - a == 1 and b != max(chs):
            hard.append(f"B5 qualifying scenes in adjacent chapters {a} and {b}")
    return hard
