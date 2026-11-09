"""§25.3 cross-book early-reveal guard (kit D1: specified in the engine, never built)."""
from __future__ import annotations

from ..bible import Bible
from ..textstats import contains_phrase, strip_md
from . import GateReport


def scan(bible: Bible, ch: int, raw: str) -> GateReport:
    rep = GateReport(ch)
    low = strip_md(raw).lower()
    fired_open = []
    for lock in bible.locks:
        probe = (lock.get("probe") or "").strip()
        if not probe or not contains_phrase(low, probe):
            continue
        if bible.lock_is_open(lock, ch):
            fired_open.append(lock["id"])
        else:
            rep.hard.append(
                f"series-lock: '{lock['id']}' unlocks at book {lock['earliest_book']} ch "
                f"{lock['earliest_chapter']}, but probe '{probe}' fired at book {bible.active_book} ch {ch}"
            )
    if fired_open:
        rep.ok.append(f"series-locks: unlocked probe(s) present: {fired_open}")
    if not rep.hard:
        rep.ok.append(f"series-locks: clear ({len(bible.locks)} locks across all books)")
    rep.meta["locks_fired_open"] = fired_open
    return rep
