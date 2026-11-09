"""§24 EXECUTABLE_STATE_LOOP: state.json (pure data) and the commit write-back."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
import tempfile
from pathlib import Path

from .bible import Bible
from .textstats import contains_phrase, profile

ENGINE_VERSION = "CHRONICLE-RUNTIME-1.0 (engine spec 23.6.1-HD)"

# Calibration carried forward from the Concord Bk1 run (kit §3). These replace the
# §24.3 template defaults, which shipped mis-calibrated (kit D5).
DEFAULT_VOICE_BANDS = {
    "sentence_mean": [12.0, 21.0],
    "sentence_median": [10, 19],
    "stdev_floor": 6.5,
    "pct_short_le5": [10, 32],
    "ellipsis_per1k": [0.4, 1.4],
    "questions_per1k": [4.0, 11.0],
    "dialogue_ratio_pct": [12, 18],
}


class CommitRefused(Exception):
    pass


def file_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, path)


def load_json(path: Path, default=None):
    if not path.exists():
        return copy.deepcopy(default)
    return json.loads(path.read_text(encoding="utf-8"))


def init_state(bible: Bible, title: str | None = None, voice_bands: dict | None = None) -> dict:
    book = bible.book
    n_ch = bible.chapter_count
    target = int(book.get("length_target_words") or n_ch * 3000)
    avg = target / n_ch
    beats = []
    for b in book["chapter_spine_beatmap"]:
        beats.append(
            {
                "chapters": list(b["chapters_approx"]),
                "pct": b.get("pct"),
                "label": b.get("intent", "")[:200],
                "load_bearing": bool(b.get("load_bearing")),
                "uf": b.get("uf", []),
            }
        )
    return {
        "meta": {
            "project_id": (title or book.get("title") or "project").lower().replace(" ", "-")[:60],
            "engine_version": ENGINE_VERSION,
            "lane": bible.lane,
            "title": title or book.get("title", ""),
            "book": bible.active_book,
            "bible_checksum": bible.checksum,
            "schema_version": 2,
        },
        "voice": {
            "pov_tense": book.get("pov_tense", ""),
            "header_format": ["# Chapter N", "### *Title*", "**POV: <Name>**"],
        },
        "length": {
            "target_words_total": [round(target * 0.95), round(target * 1.10)],
            "chapter_count_target": n_ch,
            "chapter_band": [round(avg * 0.95), round(avg * 1.10)],
            "exempt_chapters": {},
        },
        "hard_rules": {"no_em_dash": True, "uf_zero_is_hard_halt": True},
        "voice_bands": copy.deepcopy(voice_bands or DEFAULT_VOICE_BANDS),
        "voice_bands_by_pov": {},
        "chapter_overrides": {"_note": "per-chapter band overrides applied live, e.g. {'7': {'dialogue_ratio_pct': [12, 30]}}"},
        "uf_layer": {"cue_phrases": [], "credited_by_chapter": {}},
        "beat_map": {"chapters_total_planned": n_ch, "beats": beats},
        "bond_ledger": copy.deepcopy(book.get("bond_ledger", {})),
        # D5: spent_probes MUST be a dict of lists, never a string.
        "reveal_ledger": {"spent_probes": {}, "fired": []},
        "motifs": {},
        "phrase_watch": {"soft_cap_fraction": 0.30, "phrases": {}},
        "chapter_summaries": {},
        "committed_chapters": [],
        "rhythm_history": [],
        "audit_log": [],
    }


def effective_bands(state: dict, ch: int, pov: str | None) -> dict:
    """Book-wide bands, then POV bands, then per-chapter overrides (kit §3: applied live)."""
    bands = copy.deepcopy(state.get("voice_bands", DEFAULT_VOICE_BANDS))
    if pov:
        for name, b in (state.get("voice_bands_by_pov") or {}).items():
            if name.lower() in pov.lower().split() or name.lower() == pov.lower():
                bands.update(b)
    bands.update((state.get("chapter_overrides") or {}).get(str(ch), {}))
    return bands


def chapter_band(state: dict, ch: int) -> tuple[int, int]:
    lo, hi = state["length"]["chapter_band"]
    exempt = state["length"].get("exempt_chapters", {}).get(str(ch))
    if isinstance(exempt, list):
        return int(exempt[0]), int(exempt[1])
    if exempt:
        return lo, int(exempt)
    return int(lo), int(hi)


def commit(
    state: dict,
    ch: int,
    raw: str,
    pass_token: dict | None,
    *,
    title: str = "",
    pov: str = "",
    fired: list[str] | None = None,
    ufs: list[str] | None = None,
    summary: str | None = None,
) -> dict:
    """The write-back. Refuses unless THIS exact text has a PASS token (kit §4.1:
    commit must never fire on a hard fail)."""
    h = file_hash(raw)
    if not pass_token or pass_token.get("hash") != h or pass_token.get("chapter") != ch or not pass_token.get("passed"):
        raise CommitRefused(
            f"commit refused for ch{ch}: no PASSing validation on record for this exact text. "
            "Validate, read the result, then commit."
        )
    fired, ufs = fired or [], ufs or []
    low = raw.lower()
    prof = profile(raw).as_dict()
    prof.update({"chapter": ch, "title": title, "pov": pov, "uf_credited": ufs})

    for ph, info in state["phrase_watch"]["phrases"].items():
        if contains_phrase(low, ph) or any(contains_phrase(low, a) for a in info.get("also_match", [])):
            if ch not in info["chapters"]:
                info["chapters"].append(ch)
    for name, m in state["motifs"].items():
        if name.startswith("_"):
            continue
        if any(contains_phrase(low, c) for c in m.get("cues", [])) and ch not in m["used_in_chapters"]:
            m["used_in_chapters"].append(ch)
    for r in fired:
        if r not in state["reveal_ledger"]["fired"]:
            state["reveal_ledger"]["fired"].append(r)
    state["uf_layer"]["credited_by_chapter"][str(ch)] = ufs
    if summary:
        state["chapter_summaries"][str(ch)] = summary
    if ch not in state["committed_chapters"]:
        state["committed_chapters"].append(ch)
        state["committed_chapters"].sort()
    state["rhythm_history"] = sorted(
        [r for r in state["rhythm_history"] if r["chapter"] != ch] + [prof], key=lambda r: r["chapter"]
    )
    total = sum(r["words"] for r in state["rhythm_history"])
    state["audit_log"].append(
        {
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
            "chapter": ch,
            "words": prof["words"],
            "running_total": total,
            "fired": fired,
            "uf_credited": ufs,
            "hash": h[:16],
        }
    )
    return prof
