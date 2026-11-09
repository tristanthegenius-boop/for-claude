# Chronicle Runtime

Software that **runs the Chronicle Engine**. It handles the steps the engine spec
says it cannot enforce by itself (§24: "this engine is a prose document"):

| Step | Who does it |
| :--- | :--- |
| §25 series-bible intake, lane guard, every pass | runtime (code) |
| Draft chapter N under the full engine spec | any **OpenRouter** model |
| §24 validate, §25 cross-book lock scan, §26 heat gate | runtime (code) |
| §25 bible verdict, content care, §23 UF credit, EPL questions | reviewer model (JSON) |
| Surgical revision on any HARD failure or low craft score | drafting model |
| §24 commit write-back (refused unless *this exact text* passed) | runtime (code) |
| Continuity summaries, state ledgers, heat delivery, adherence log | runtime (code) |

It is genre-neutral. The bible's `manifest.lane` / `series.genre` decides the lane,
the engine file you load supplies the Genre Kernel, and lanes with no
`heat_delivery` block never see the heat gate. There is no graft protocol.

## Fixes over the engine's embedded scripts (Concord Bk1 defects)

- **D1:** cross-book reveal-lock scan built in and run on every chapter.
- **D2:** `lint-bible` flags generic probes ("the ledger", "who paid", single words) and probes already present in shipped books.
- **D3:** dialogue is parsed per paragraph. Missing close-quotes HARD-fail; multi-paragraph speech is legal; curly quotes work.
- **D4:** the heat span is located inside one scene, so breaks elsewhere aren't cutaways; "by the time I" is no longer a cutaway.
- **D5:** `state.json` is calibrated from the bible (band = target/chapters × 0.95 to 1.10), `spent_probes` is `{}`, and ellipsis/question bands come from the Bk1 calibration.
- **Process:** commit refuses to run after a failed gate. You can't chain it by mistake.

## Quick start

```bash
pip install -e '.[docx]'
export OPENROUTER_API_KEY=sk-or-...      # or put it in <project>/.env

chronicle models claude                   # search OpenRouter model ids
chronicle -p projects/concord init \
  --engine Chronicle_Engine_v23_6_1_HD_INTAKE_MM.txt \
  --bible series_bible.json --book 2 \
  --working-bible The_Concord_Book2_Working_Bible.md \
  --kernel-lane "MM Mafia Omegaverse" \
  --model <draft-model-id> --review-model <review-model-id>

chronicle -p projects/concord run            # writes ch1..N, stops at breakpoints/failures
chronicle -p projects/concord status
chronicle -p projects/concord export --docx
```

Put your engine, bibles and manuscripts under `projects/`. That folder is git-ignored,
so they are never committed.

## Commands

| Command | What it does |
| :--- | :--- |
| `init` | copy engine + bible into a project, WSO_INIT `state.json`, lint the bible |
| `intake` | §25 intake check (exit 2 on hard stop) |
| `lint-bible [books...]` | weak-probe report (D2) |
| `write N` | draft → gates → review → revise (up to `max_attempts`) → commit |
| `run [--from N] [--to M]` | `write` in sequence; stops at `breakpoints` or a chapter that needs you |
| `check FILE N` / `commit FILE N` | manual path: gate your own edited chapter, then write it back |
| `status` | chapters, words, heat budget, fired reveals |
| `book-done` | §26.4 book-level gate + completeness + length |
| `next-book` | archive book N to `bookN/`, advance `active_book`, re-init state |
| `calibrate FILES --pov NAME --write` | measure voice bands from your own chapters |
| `export [--docx]` | assemble the manuscript |

## Config (`chronicle.json`)

```json
{
  "models": {"draft": "<id>", "review": "<id>", "summary": "<cheap id>"},
  "temperature": 0.85, "max_tokens": 16000,
  "max_attempts": 5, "craft_passes": 1, "min_craft_score": 8,
  "breakpoints": [5, 10, 15]
}
```

Per-chapter band overrides go in `state.json` under `chapter_overrides`, e.g.
`{"7": {"dialogue_ratio_pct": [12, 30]}}`. They apply live at validation.

**Cost note:** the engine (~60k tokens, with the embedded scripts stripped) is the system prompt
on every call. Anthropic models on OpenRouter get prompt caching automatically.
Use a cheaper model for `summary`.

**Publishing note:** Amazon KDP requires you to disclose AI-generated text when you publish.

## Tests

```bash
pytest -q
```
