"""§25 SERIES-BIBLE INTAKE: hard-stop loader, lane guard, and plan lookups.

Genre-neutral: reads series.genre / manifest.lane and never assumes a lane.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .textstats import contains_phrase

SCHEMA_MAJOR = "1"


class IntakeError(Exception):
    """Raised where the engine would HARD STOP (exit 2)."""


@dataclass
class Bible:
    data: dict
    checksum: str
    path: Path | None = None

    # ── lookups ──────────────────────────────────────────────────────────
    @property
    def active_book(self) -> int:
        return int(self.data["manifest"]["active_book"])

    @property
    def book(self) -> dict:
        return self.data["books"][str(self.active_book)]

    @property
    def genre(self) -> str:
        return self.data["series"].get("genre") or self.data["manifest"].get("lane", "unspecified")

    @property
    def lane(self) -> str:
        return self.data["manifest"].get("lane") or self.genre

    @property
    def locks(self) -> list[dict]:
        return self.data["series"]["reveal_schedule"].get("locks", [])

    @property
    def already_known(self) -> list[str]:
        return self.data["series"]["reveal_schedule"].get("already_known", [])

    @property
    def content_care(self):
        return self.data["series"].get("content_care_LOCKED", [])

    @property
    def conflict_policy(self) -> str:
        return self.data["manifest"].get("conflict_policy", "manuscript_wins_flag")

    @property
    def chapter_count(self) -> int:
        n = self.book.get("chapter_count")
        if n:
            return int(n)
        return max(b["chapters_approx"][1] for b in self.book["chapter_spine_beatmap"])

    def beat(self, ch: int) -> dict | None:
        for b in self.book["chapter_spine_beatmap"]:
            lo, hi = b["chapters_approx"]
            if lo <= ch <= hi:
                return b
        return None

    def heat_rows(self, ch: int | None = None) -> list[dict]:
        rows = self.book.get("heat_delivery") or []
        return rows if ch is None else [r for r in rows if r.get("chapter") == ch]

    def lock_is_open(self, lock: dict, ch: int, book: int | None = None) -> bool:
        book = self.active_book if book is None else book
        eb, ec = int(lock["earliest_book"]), int(lock["earliest_chapter"])
        return book > eb or (book == eb and ch >= ec)

    def locked_probes(self, ch: int) -> list[dict]:
        """Locks whose probe must NOT appear in chapter `ch` of the active book."""
        return [l for l in self.locks if l.get("probe") and not self.lock_is_open(l, ch)]

    def with_active_book(self, n: int) -> "Bible":
        """emit_view equivalent: same bible, different active book."""
        d = copy.deepcopy(self.data)
        if str(n) not in d["books"]:
            raise IntakeError(f"book {n} not present in books{{}}.")
        d["manifest"]["active_book"] = n
        raw = json.dumps(d, sort_keys=True)
        return Bible(d, hashlib.sha256(raw.encode()).hexdigest()[:16], self.path)

    def intake_banner(self) -> str:
        rows = self.heat_rows()
        heat = (
            f"HEAT BUDGET: {len(rows)} scenes at ch " + ", ".join(str(r.get("chapter")) for r in rows)
            if rows
            else "HEAT BUDGET: none (lane exempt from §26)"
        )
        return (
            f"SERIES BIBLE INTAKEN [{self.checksum}] active book {self.active_book} "
            f"(genre: {self.genre}), {len(self.locks)} series locks. {heat}"
        )


def _validate(b: dict) -> None:
    schema = b.get("schema", "")
    if not schema.startswith("series-bible/"):
        raise IntakeError(f"not a series bible (schema='{schema}').")
    if schema.split("/")[1].split(".")[0] != SCHEMA_MAJOR:
        raise IntakeError(f"schema major mismatch: engine expects {SCHEMA_MAJOR}.x, got '{schema}'.")
    man = b.get("manifest") or {}
    active = man.get("active_book")
    if active is None:
        raise IntakeError("manifest.active_book missing.")
    books = b.get("books") or {}
    if str(active) not in books:
        raise IntakeError(f"active_book {active} not present in books{{}}.")
    if not books[str(active)].get("chapter_spine_beatmap"):
        raise IntakeError(f"active book {active} has no chapter_spine_beatmap.")
    if not (b.get("series") or {}).get("reveal_schedule"):
        raise IntakeError("series.reveal_schedule missing.")


def intake(path: str | Path, active_book: int | None = None) -> Bible:
    p = Path(path)
    if not p.exists():
        raise IntakeError(f"series bible not found at {p}.")
    raw = p.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except Exception as e:  # noqa: BLE001
        raise IntakeError(f"series bible failed to parse: {e}") from e
    if active_book is not None:
        data.setdefault("manifest", {})["active_book"] = active_book
    _validate(data)
    return Bible(data, hashlib.sha256(raw.encode()).hexdigest()[:16], p)


def _norm_lane(s: str) -> str:
    s = s.lower().replace("omegaverse", "ov")
    return re.sub(r"[^a-z0-9]", "", s)


def lane_guard(bible: Bible, kernel_lane: str | None) -> None:
    """§25.5b KERNEL_LANE_MISMATCH. A wrong-lane kernel must halt, not pass silently."""
    if not kernel_lane:
        return
    if _norm_lane(kernel_lane) not in (_norm_lane(bible.lane), _norm_lane(bible.genre)):
        raise IntakeError(
            f"KERNEL_LANE_MISMATCH: bible declares '{bible.lane}', resident kernel is '{kernel_lane}'."
        )


# ── D2: lock-probe lint ──────────────────────────────────────────────────
COMMON = set(
    """the a an of to in on at by for with from who what which that this those these is was
    be been no not true copy over passed paid ledger book books said says one two all any
    some it its his her their our my your we they he she i you and or but if as so""".split()
)


def lint_probes(bible: Bible, manuscripts: dict[str, str] | None = None) -> list[str]:
    """Flag reveal-lock probes that will false-positive (kit D2).

    A probe made only of everyday words ('the ledger', 'who paid') will fire on
    ordinary prose. If shipped manuscripts are supplied, any probe already present
    in a book written before its unlock is reported with the hit count.
    """
    issues = []
    for l in bible.locks:
        probe = (l.get("probe") or "").strip()
        if not probe:
            issues.append(f"{l.get('id')}: no probe (lock cannot be machine-checked)")
            continue
        toks = re.findall(r"[a-z']+", probe.lower())
        if len(toks) == 1 or (len(toks) <= 3 and all(t in COMMON or len(t) <= 4 for t in toks)):
            issues.append(
                f"{l['id']}: probe '{probe}' is generic English and will false-positive; "
                "reword it to the consequence, not the noun"
            )
        for name, text in (manuscripts or {}).items():
            low = text.lower()
            if contains_phrase(low, probe):
                n = len(re.findall(re.escape(probe.lower()), low))
                issues.append(f"{l['id']}: probe '{probe}' already occurs {n}x in {name}")
    return issues
