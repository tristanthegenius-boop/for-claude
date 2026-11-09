"""Text measurement shared by every gate (ports of the §24 helpers, with fixes)."""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field

WORD_RE = re.compile(r"[\w']+")
# Scene-break markers. `·` (U+00B7) is the Concord house separator (kit D4 workaround).
SCENE_BREAK_RE = re.compile(r"^\s*(?:-{3,}|\*\s*\*\s*\*|#|~{3,}|·|·\s*·\s*·)\s*$")

CURLY = {"“": '"', "”": '"', "‘": "'", "’": "'"}


def normalize_quotes(t: str) -> str:
    for k, v in CURLY.items():
        t = t.replace(k, v)
    return t


def words(t: str) -> list[str]:
    return WORD_RE.findall(t)


def sentences(t: str) -> list[str]:
    parts = re.split(r'(?<=[.!?])["”’)]*\s+', t)
    return [s for s in parts if re.search(r"\w", s)]


def is_header_line(line: str) -> bool:
    s = line.strip()
    return s.startswith("#") or s.startswith("**POV")


def strip_md(t: str) -> str:
    """Prose only: drop headers, POV line and scene-break markers."""
    return "\n".join(
        l for l in t.splitlines() if not is_header_line(l) and not SCENE_BREAK_RE.match(l)
    )


def per1k(count: int, wc: int) -> float:
    return 1000.0 * count / max(1, wc)


def paragraphs(t: str) -> list[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]


def scenes(raw: str) -> list[str]:
    """Split a chapter into scenes at scene-break marker lines."""
    out, cur = [], []
    for line in raw.splitlines():
        if SCENE_BREAK_RE.match(line):
            out.append("\n".join(cur))
            cur = []
        elif not is_header_line(line):
            cur.append(line)
    out.append("\n".join(cur))
    return [s for s in out if s.strip()]


@dataclass
class DialogueReport:
    ratio_pct: float
    errors: list[str] = field(default_factory=list)


def dialogue_report(t: str) -> DialogueReport:
    """Paragraph-scoped quote parsing (fixes kit D3).

    The engine's original `re.findall(r'"([^"]*)"')` paired quotes across the whole
    chapter, so one missing close-quote (or a legal multi-paragraph speech) made it
    count narration as dialogue. Here quotes are tracked per paragraph. A paragraph
    that ends with a quote still open is legal only when the next paragraph opens
    with a quote (multi-paragraph speech); otherwise it is a missing close-quote.
    """
    paras = paragraphs(normalize_quotes(strip_md(t)))
    total = sum(len(words(p)) for p in paras)
    dlg_words, errors = 0, []
    for i, p in enumerate(paras):
        # apostrophes are single quotes; only double quotes delimit speech
        segs = p.split('"')
        inside = segs[1::2]
        dlg_words += sum(len(words(s)) for s in inside)
        if p.count('"') % 2 == 1:
            nxt = paras[i + 1] if i + 1 < len(paras) else ""
            if not nxt.startswith('"'):
                snippet = p[:60].replace("\n", " ")
                errors.append(f"unclosed quote in paragraph {i + 1}: '{snippet}...'")
    return DialogueReport(100.0 * dlg_words / max(1, total), errors)


@dataclass
class Profile:
    words: int
    sent_mean: float
    sent_median: float
    stdev: float
    pct_short_le5: float
    ellipsis_per1k: float
    questions_per1k: float
    exclamations_per1k: float
    semicolons_per1k: float
    em_dashes: int
    dialogue_ratio_pct: float
    max_opener_run: int

    def as_dict(self) -> dict:
        return {k: (round(v, 2) if isinstance(v, float) else v) for k, v in self.__dict__.items()}


def max_opener_run(prose: str) -> int:
    firsts = [(words(s) or [""])[0].lower() for s in sentences(prose)]
    run = mx = 1 if firsts else 0
    for i in range(1, len(firsts)):
        run = run + 1 if firsts[i] and firsts[i] == firsts[i - 1] else 1
        mx = max(mx, run)
    return mx


def profile(raw: str) -> Profile:
    prose = strip_md(raw)
    wc = len(words(prose))
    slens = [len(words(s)) for s in sentences(prose)] or [0]
    return Profile(
        words=wc,
        sent_mean=statistics.mean(slens),
        sent_median=statistics.median(slens),
        stdev=statistics.pstdev(slens),
        pct_short_le5=100.0 * sum(1 for x in slens if x <= 5) / len(slens),
        ellipsis_per1k=per1k(len(re.findall(r"\.\.\.|…", prose)), wc),
        questions_per1k=per1k(prose.count("?"), wc),
        exclamations_per1k=per1k(prose.count("!"), wc),
        semicolons_per1k=per1k(prose.count(";"), wc),
        em_dashes=prose.count("—") + len(re.findall(r"(?<!-)--(?!-)", prose)),
        dialogue_ratio_pct=dialogue_report(raw).ratio_pct,
        max_opener_run=max_opener_run(prose),
    )


def contains_phrase(text_lower: str, phrase: str) -> bool:
    """Word-boundary phrase match (so 'mark' does not hit 'market')."""
    p = phrase.lower().strip()
    if not p:
        return False
    return re.search(r"(?<![\w])" + re.escape(p) + r"(?![\w])", text_lower) is not None
