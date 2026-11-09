"""§24.1 validate: the mechanical presentation gate (port with kit fixes D3, D5)."""
from __future__ import annotations

import re

from ..state import chapter_band, effective_bands
from ..textstats import contains_phrase, dialogue_report, profile, strip_md, words
from . import GateReport

HEADER_POV = re.compile(r"\*\*POV:\s*([^*]+?)\*\*")
BITE_RE = re.compile(
    r"(claiming bite|claims? (him|her|me) for the first|first claim|bonds? (him|her|me) for the first|sinks? (his|her) teeth)"
)


def parse_header(raw: str) -> dict:
    head = "\n".join(raw.splitlines()[:8])
    num = re.search(r"^#\s*Chapter\s*(\d+)\b", head, re.M)
    title = re.search(r"^###\s*\*(.+?)\*", head, re.M)
    pov = HEADER_POV.search(head)
    return {
        "number": int(num.group(1)) if num else None,
        "title": title.group(1).strip() if title else "",
        "pov": pov.group(1).strip() if pov else "",
    }


def _in(v, band) -> bool:
    return band[0] <= v <= band[1]


def validate(state: dict, ch: int, raw: str) -> GateReport:
    rep = GateReport(ch)
    prose = strip_md(raw)
    low = prose.lower()
    hdr = parse_header(raw)
    rep.meta["header"] = hdr

    # HEADER (hard)
    if hdr["number"] != ch:
        rep.hard.append(f"header: missing '# Chapter {ch}'")
    if not hdr["title"]:
        rep.hard.append("header: missing '### *Title*'")
    if not hdr["pov"]:
        rep.hard.append("header: missing '**POV: <Name>**'")
    if hdr["number"] == ch and hdr["title"] and hdr["pov"]:
        rep.ok.append(f"header: ch{ch} '{hdr['title']}' POV={hdr['pov']}")

    prof = profile(raw)
    rep.meta["profile"] = prof.as_dict()

    # EM-DASH (hard)
    if state.get("hard_rules", {}).get("no_em_dash", True):
        if prof.em_dashes:
            rep.hard.append(f"em-dash: {prof.em_dashes} (HARD BAN)")
        else:
            rep.ok.append("em-dash: 0")

    # WORD BAND (hard)
    lo, hi = chapter_band(state, ch)
    if not lo <= prof.words <= hi:
        rep.hard.append(f"word count: {prof.words} outside [{lo},{hi}]")
    else:
        rep.ok.append(f"word count: {prof.words} in [{lo},{hi}]")

    # TENSE (warn, past lock)
    tense = (state.get("voice", {}).get("pov_tense") or "past").lower()
    if "past" in tense:
        past = len(re.findall(r"\b(walked|said|asked|looked|turned|felt|knew|was|were|had)\b", prose))
        pres = len(re.findall(r"\b(walks|says|asks|looks|turns|feels|knows|is|are)\b", prose))
        if pres > past * 0.8:
            rep.warn.append(f"tense: present markers ({pres}) high vs past ({past}); tense lock is PAST")

    # DIALOGUE QUOTES (hard on unclosed quote, kit D3)
    dr = dialogue_report(raw)
    for e in dr.errors:
        rep.hard.append(f"quotes: {e}")

    # BEAT
    beat = next(
        (b for b in state["beat_map"]["beats"] if b["chapters"][0] <= ch <= b["chapters"][1]), None
    )
    if not beat:
        rep.hard.append(f"beat: ch{ch} not in any planned beat range")
    else:
        rep.ok.append(f"beat: '{beat['label'][:60]}'")

    # §23 UF (mechanical half; the reviewer credits UFs semantically)
    cues = state.get("uf_layer", {}).get("cue_phrases", [])
    if cues:
        present = [c for c in cues if contains_phrase(low, c)]
        if present:
            rep.ok.append(f"UF cues present: {present[:3]}")
        else:
            rep.warn.append("UF cues: none of the cue phrases present (reviewer will judge UF credit)")

    # SPENT-REVEAL RESTAGE (D5: tolerate a malformed string instead of iterating chars)
    spent = state.get("reveal_ledger", {}).get("spent_probes") or {}
    restaged = []
    if isinstance(spent, dict):
        for label, probes in spent.items():
            if label.startswith("_"):
                continue
            if isinstance(probes, str):
                probes = [probes]
            restaged += [(label, p) for p in probes if contains_phrase(low, p)]
    else:
        rep.warn.append("state: reveal_ledger.spent_probes is not a dict; ignored (fix to {})")
    if len(restaged) >= 2:
        rep.hard.append("reveal: possible restage of SPENT reveals: " + "; ".join(p for _, p in restaged))
    elif restaged:
        rep.warn.append(f"reveal: phrase resembling spent reveal '{restaged[0][1]}'; confirm referenced as KNOWN")

    # BOND GUARD (hard)
    for edge, info in (state.get("bond_ledger") or {}).items():
        if edge.startswith("_") or not isinstance(info, dict):
            continue
        bonded = str(info.get("state", "")).upper().startswith(("BONDED", "MATE", "SOUL", "CLAIMED"))
        if bonded and not info.get("may_first_claim", True):
            for m in BITE_RE.finditer(low):
                ctx = low[max(0, m.start() - 70) : m.end() + 70]
                if any(len(n) > 3 and n in ctx for n in re.findall(r"[a-z]+", edge.lower())):
                    rep.hard.append(f"bond-guard: possible FIRST-CLAIM on bonded edge '{edge}' near '{m.group(0)}'")
                    break

    # OPENER MONOTONY
    if prof.max_opener_run >= 5:
        rep.hard.append(f"opener: {prof.max_opener_run} consecutive same-opener sentences (HARD >=5)")
    elif prof.max_opener_run == 4:
        rep.warn.append("opener: 4 consecutive same-opener sentences")

    # VOICE BANDS (warn)
    vb = effective_bands(state, ch, hdr["pov"])
    checks = [
        ("sentence mean", prof.sent_mean, vb.get("sentence_mean")),
        ("sentence median", prof.sent_median, vb.get("sentence_median")),
        ("short<=5 pct", prof.pct_short_le5, vb.get("pct_short_le5")),
        ("ellipsis/1k", prof.ellipsis_per1k, vb.get("ellipsis_per1k")),
        ("questions/1k", prof.questions_per1k, vb.get("questions_per1k")),
        ("dialogue %", dr.ratio_pct, vb.get("dialogue_ratio_pct")),
    ]
    for name, v, band in checks:
        if not band:
            continue
        (rep.ok if _in(v, band) else rep.warn).append(
            f"voice: {name} {v:.1f}" + ("" if _in(v, band) else f" outside {band}")
        )
    if vb.get("stdev_floor") and prof.stdev < vb["stdev_floor"]:
        rep.warn.append(f"voice: sentence stdev {prof.stdev:.1f} < floor {vb['stdev_floor']} (mechanical rhythm)")

    # PHRASE CRUTCHES / MOTIF CAPS (warn)
    n = len(state.get("committed_chapters", [])) + 1
    pw = state.get("phrase_watch", {})
    for ph, info in pw.get("phrases", {}).items():
        if contains_phrase(low, ph) or any(contains_phrase(low, a) for a in info.get("also_match", [])):
            fut = sorted(set(info.get("chapters", []) + [ch]))
            if len(fut) / n > pw.get("soft_cap_fraction", 0.3) and n >= 4:
                rep.warn.append(f"phrase: '{ph}' now in {len(fut)}/{n} chapters")
    for name, m in (state.get("motifs") or {}).items():
        if name.startswith("_"):
            continue
        if any(contains_phrase(low, c) for c in m.get("cues", [])):
            fut = sorted(set(m.get("used_in_chapters", []) + [ch]))
            if len(fut) > m.get("cap", 99):
                rep.warn.append(f"motif: '{name}' would hit {len(fut)} > cap {m['cap']}")
    rep.meta["words"] = len(words(prose))
    return rep
