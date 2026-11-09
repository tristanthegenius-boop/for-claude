"""Gate runner: validate (§24) + series locks (§25) + heat (§26) as one report."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..bible import Bible


@dataclass
class GateReport:
    chapter: int
    ok: list[str] = field(default_factory=list)
    warn: list[str] = field(default_factory=list)
    hard: list[str] = field(default_factory=list)
    meta: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.hard

    def extend(self, other: "GateReport") -> None:
        self.ok += other.ok
        self.warn += other.warn
        self.hard += other.hard
        self.meta.update(other.meta)

    def render(self, title: str = "") -> str:
        lines = [f"=== GATES ch{self.chapter}{(': ' + title) if title else ''} ==="]
        lines += [f" [ok] {o}" for o in self.ok]
        lines += [f" [WARN] {w}" for w in self.warn]
        lines += [f" [HARD] {h}" for h in self.hard]
        lines.append(
            f"RESULT: HARD FAIL ({len(self.hard)} blocker(s), {len(self.warn)} warning(s))."
            if self.hard
            else f"RESULT: PASS ({len(self.warn)} warning(s), author discretion)."
        )
        return "\n".join(lines)


def run_all(bible: Bible, state: dict, ch: int, raw: str) -> GateReport:
    from .heat import check_chapter
    from .locks import scan
    from .validate import validate

    rep = validate(state, ch, raw)
    rep.extend(scan(bible, ch, raw))
    rep.extend(check_chapter(bible, ch, raw))
    return rep
