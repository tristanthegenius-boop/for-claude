"""A project directory: config, engine, bible, state and the workspace files."""
from __future__ import annotations

import datetime as dt
import json
import os
from dataclasses import dataclass
from pathlib import Path

from .bible import Bible, intake, lane_guard
from .state import atomic_write_json, file_hash, load_json

CONFIG_NAME = "chronicle.json"

DEFAULT_CONFIG = {
    "engine": "engine.txt",
    "bible": "series_bible.json",
    "working_bible": None,
    "active_book": None,
    "kernel_lane": None,
    "models": {"draft": None, "review": None, "summary": None},
    "temperature": 0.85,
    "max_tokens": 16000,
    "max_attempts": 5,
    "craft_passes": 1,
    "min_craft_score": 8,
    "breakpoints": [],
}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class Project:
    root: Path

    def __post_init__(self):
        self.root = Path(self.root).resolve()
        load_dotenv(self.root / ".env")

    # ── paths ────────────────────────────────────────────────────────────
    @property
    def config_path(self) -> Path:
        return self.root / CONFIG_NAME

    def p(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    @property
    def state_path(self) -> Path:
        return self.p("state.json")

    def chapter_path(self, ch: int) -> Path:
        return self.p("chapters", f"ch{ch:02d}.md")

    def draft_path(self, ch: int, attempt: int) -> Path:
        return self.p("drafts", f"ch{ch:02d}_a{attempt}.md")

    # ── config / inputs ──────────────────────────────────────────────────
    @property
    def config(self) -> dict:
        cfg = json.loads(json.dumps(DEFAULT_CONFIG))
        if self.config_path.exists():
            user = json.loads(self.config_path.read_text())
            models = {**cfg["models"], **(user.get("models") or {})}
            cfg.update(user)
            cfg["models"] = models
        return cfg

    def save_config(self, cfg: dict) -> None:
        atomic_write_json(self.config_path, cfg)

    def model(self, role: str) -> str:
        m = self.config["models"].get(role) or self.config["models"].get("draft")
        if not m:
            raise SystemExit(
                f"No model set for '{role}'. Set models.draft in {CONFIG_NAME} "
                "(any OpenRouter id; run `chronicle models <search>` to find one)."
            )
        return m

    def engine_text(self) -> str:
        return self.p(self.config["engine"]).read_text(encoding="utf-8")

    def working_bible(self) -> str | None:
        wb = self.config.get("working_bible")
        return self.p(wb).read_text(encoding="utf-8") if wb else None

    def bible(self) -> Bible:
        """§25.2: intake runs on EVERY pass, never cached."""
        b = intake(self.p(self.config["bible"]), self.config.get("active_book"))
        lane_guard(b, self.config.get("kernel_lane"))
        return b

    # ── workspace json files ─────────────────────────────────────────────
    def state(self) -> dict:
        if not self.state_path.exists():
            raise SystemExit("state.json missing. Run `chronicle init` first.")
        return load_json(self.state_path)

    def save_state(self, st: dict) -> None:
        atomic_write_json(self.state_path, st)

    def tokens(self) -> dict:
        return load_json(self.p(".chronicle", "pass_tokens.json"), {})

    def record_pass(self, ch: int, raw: str, passed: bool) -> dict:
        toks = self.tokens()
        tok = {"chapter": ch, "hash": file_hash(raw), "passed": passed,
               "ts": dt.datetime.now().isoformat(timespec="seconds")}
        toks[str(ch)] = tok
        atomic_write_json(self.p(".chronicle", "pass_tokens.json"), toks)
        return tok

    def heat_delivery(self) -> dict:
        return load_json(self.p("heat_delivery.json"), {"delivered": {}, "log": []})

    def save_heat_delivery(self, d: dict) -> None:
        atomic_write_json(self.p("heat_delivery.json"), d)

    def log_adherence(self, ch: int, review: dict) -> None:
        path = self.p("bible_adherence.json")
        d = load_json(path, {})
        d[str(ch)] = {k: review.get(k) for k in ("bible_verdict", "verdict_reason", "craft_score")}
        atomic_write_json(path, d)

    def log_event(self, event: dict) -> None:
        self.p("logs").mkdir(parents=True, exist_ok=True)
        event = {"ts": dt.datetime.now().isoformat(timespec="seconds"), **event}
        with open(self.p("logs", "run.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
