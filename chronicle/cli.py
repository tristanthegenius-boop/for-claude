"""chronicle: command-line runtime for the Chronicle Engine."""
from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
from pathlib import Path

from .bible import IntakeError, lint_probes
from .gates import run_all
from .project import CONFIG_NAME, DEFAULT_CONFIG, Project
from .state import CommitRefused, commit, init_state
from .textstats import profile


def _pj(a) -> Project:
    return Project(Path(a.project))


def cmd_init(a):
    root = Path(a.project)
    root.mkdir(parents=True, exist_ok=True)
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    for key, src in (("engine", a.engine), ("bible", a.bible), ("working_bible", a.working_bible)):
        if src:
            dst = root / Path(src).name
            if Path(src).resolve() != dst.resolve():
                shutil.copy(src, dst)
            cfg[key] = dst.name
    cfg["active_book"] = a.book
    cfg["kernel_lane"] = a.kernel_lane
    cfg["models"] = {"draft": a.model, "review": a.review_model or a.model, "summary": a.summary_model or a.review_model or a.model}
    pj = Project(root)
    previous = pj.config_path.read_text() if pj.config_path.exists() else None
    pj.save_config(cfg)
    try:
        bible = pj.bible()
    except IntakeError:
        if previous is None:
            pj.config_path.unlink()
        else:
            pj.config_path.write_text(previous)
        raise
    print(bible.intake_banner())
    if pj.state_path.exists() and not a.force:
        print("state.json exists; keeping it (use --force to re-initialise).")
    else:
        pj.save_state(init_state(bible, a.title))
        print(f"WSO_INIT: wrote {pj.state_path}")
    env = root / ".env"
    if not env.exists():
        env.write_text("# OPENROUTER_API_KEY=sk-or-...\n")
    issues = lint_probes(bible)
    if issues:
        print("\nBIBLE LINT (fix in the codex before relying on these locks):")
        for i in issues:
            print(" -", i)


def cmd_intake(a):
    print(_pj(a).bible().intake_banner())


def cmd_lint(a):
    mss = {Path(f).name: Path(f).read_text(encoding="utf-8") for f in a.manuscripts}
    issues = lint_probes(_pj(a).bible(), mss)
    print("\n".join(" - " + i for i in issues) or "lint: clean")


def cmd_check(a):
    pj = _pj(a)
    raw = Path(a.file).read_text(encoding="utf-8")
    rep = run_all(pj.bible(), pj.state(), a.chapter, raw)
    print(rep.render(Path(a.file).name))
    pj.record_pass(a.chapter, raw, rep.passed)
    sys.exit(0 if rep.passed else 1)


def cmd_commit(a):
    pj = _pj(a)
    raw = Path(a.file).read_text(encoding="utf-8")
    st = pj.state()
    from .gates.validate import parse_header

    hdr = parse_header(raw)
    try:
        prof = commit(st, a.chapter, raw, pj.tokens().get(str(a.chapter)), title=hdr["title"],
                      pov=hdr["pov"], fired=a.fired, ufs=a.uf, summary=a.summary)
    except CommitRefused as e:
        print(e)
        sys.exit(1)
    pj.chapter_path(a.chapter).parent.mkdir(parents=True, exist_ok=True)
    pj.chapter_path(a.chapter).write_text(raw, encoding="utf-8")
    pj.save_state(st)
    print(f"COMMITTED ch{a.chapter} '{prof['title']}' ({prof['words']}w). committed: {st['committed_chapters']}")


def _orch(a):
    from .llm import OpenRouter
    from .orchestrator import Orchestrator

    llm = OpenRouter()
    return Orchestrator(_pj(a), llm), llm


def cmd_write(a):
    orch, llm = _orch(a)
    r = orch.write_chapter(a.chapter)
    print(f"\n{r.status} after {r.attempts} attempt(s). usage: {llm.usage}")
    sys.exit(0 if r.status == "committed" else 1)


def cmd_run(a):
    pj = _pj(a)
    st = pj.state()
    start = a.start or (max(st["committed_chapters"], default=0) + 1)
    end = a.end or st["beat_map"]["chapters_total_planned"]
    orch, llm = _orch(a)
    res = orch.run(start, end)
    print(f"\nran {len(res)} chapter(s); usage: {llm.usage}")


def cmd_status(a):
    pj = _pj(a)
    bible = pj.bible()
    st = pj.state()
    print(bible.intake_banner())
    total = sum(r["words"] for r in st["rhythm_history"])
    print(f"book {bible.active_book}: {st['meta'].get('title')}  committed {len(st['committed_chapters'])}/"
          f"{bible.chapter_count}  words {total} / target {st['length']['target_words_total']}")
    for r in st["rhythm_history"]:
        print(f"  ch{r['chapter']:>2} {r['words']:>5}w  mean {r['sent_mean']:>5}  POV {r['pov']:<10} {r['title']}")
    hd = pj.heat_delivery().get("delivered", {})
    for row in bible.heat_rows():
        c = str(row.get("chapter"))
        print(f"  heat {row.get('scene_id')} ch{c}: {'delivered' if hd.get(c) else 'outstanding'}")
    print(f"  reveals fired: {st['reveal_ledger']['fired']}")


def cmd_book_done(a):
    from .orchestrator import Orchestrator

    hard = Orchestrator(_pj(a), None).book_done()
    for h in hard:
        print(" [HARD]", h)
    print("BOOK_DONE:", "HARD FAIL" if hard else "PASS")
    sys.exit(1 if hard else 0)


def cmd_next_book(a):
    pj = _pj(a)
    cfg = pj.config
    cur = pj.bible().active_book
    if a.archive:
        dst = pj.p(f"book{cur}")
        dst.mkdir(exist_ok=True)
        for name in ("chapters", "drafts", "state.json", "heat_delivery.json", "bible_adherence.json", "logs"):
            if pj.p(name).exists():
                shutil.move(str(pj.p(name)), str(dst / name))
        pj.p(".chronicle", "pass_tokens.json").unlink(missing_ok=True)
    cfg["active_book"] = cur + 1
    pj.save_config(cfg)
    bible = pj.bible()
    pj.save_state(init_state(bible))
    print(bible.intake_banner())
    print(f"book {cur} archived to book{cur}/; state.json initialised for book {cur + 1}")


def cmd_calibrate(a):
    pj = _pj(a)
    profs = [profile(Path(f).read_text(encoding="utf-8")) for f in a.files]
    def band(vals, pad):
        m = statistics.mean(vals)
        sd = statistics.pstdev(vals) if len(vals) > 1 else m * 0.1
        return [round(max(0, m - pad * max(sd, m * 0.08)), 1), round(m + pad * max(sd, m * 0.08), 1)]
    bands = {
        "sentence_mean": band([p.sent_mean for p in profs], 1.5),
        "sentence_median": band([p.sent_median for p in profs], 1.5),
        "pct_short_le5": band([p.pct_short_le5 for p in profs], 1.5),
        "ellipsis_per1k": band([p.ellipsis_per1k for p in profs], 1.5),
        "questions_per1k": band([p.questions_per1k for p in profs], 1.5),
        "dialogue_ratio_pct": band([p.dialogue_ratio_pct for p in profs], 1.5),
        "stdev_floor": round(min(p.stdev for p in profs) * 0.9, 1),
    }
    print(json.dumps(bands, indent=1))
    if a.write:
        st = pj.state()
        if a.pov:
            st.setdefault("voice_bands_by_pov", {})[a.pov] = bands
        else:
            st["voice_bands"].update(bands)
        pj.save_state(st)
        print(f"written to state.json ({'POV ' + a.pov if a.pov else 'book-wide'})")


def cmd_export(a):
    from .export import export_docx, export_markdown

    pj = _pj(a)
    out = pj.p("manuscript.docx" if a.docx else "manuscript.md")
    (export_docx if a.docx else export_markdown)(pj, out)
    print(f"wrote {out}")


def cmd_models(a):
    from .llm import list_models

    for m in list_models(a.query)[:60]:
        pr = m.get("pricing", {})
        print(f"{m['id']:<55} ctx {m.get('context_length', '?'):>8}  in ${pr.get('prompt', '?')}/tok  out ${pr.get('completion', '?')}/tok")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="chronicle", description="Runtime for the Chronicle Engine (OpenRouter).")
    ap.add_argument("-p", "--project", default=".", help="project directory (default: current)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create a project from an engine file + series bible")
    s.add_argument("--engine", required=True)
    s.add_argument("--bible", required=True)
    s.add_argument("--working-bible")
    s.add_argument("--book", type=int)
    s.add_argument("--title")
    s.add_argument("--model", help="OpenRouter model id for drafting")
    s.add_argument("--review-model")
    s.add_argument("--summary-model")
    s.add_argument("--kernel-lane", help="lane the engine's resident Genre Kernel declares (§25.5b guard)")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_init)

    sub.add_parser("intake", help="§25 intake check").set_defaults(fn=cmd_intake)
    s = sub.add_parser("lint-bible", help="flag weak reveal-lock probes (D2)")
    s.add_argument("manuscripts", nargs="*")
    s.set_defaults(fn=cmd_lint)

    s = sub.add_parser("check", help="run all gates on a chapter file")
    s.add_argument("file"); s.add_argument("chapter", type=int)
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("commit", help="write back a PASSing chapter")
    s.add_argument("file"); s.add_argument("chapter", type=int)
    s.add_argument("--fired", nargs="*", default=[]); s.add_argument("--uf", nargs="*", default=[])
    s.add_argument("--summary")
    s.set_defaults(fn=cmd_commit)

    s = sub.add_parser("write", help="draft, gate, revise and commit one chapter")
    s.add_argument("chapter", type=int)
    s.set_defaults(fn=cmd_write)

    s = sub.add_parser("run", help="write chapters in sequence until done, a breakpoint, or a failure")
    s.add_argument("--from", dest="start", type=int); s.add_argument("--to", dest="end", type=int)
    s.set_defaults(fn=cmd_run)

    sub.add_parser("status", help="progress, words, heat budget").set_defaults(fn=cmd_status)
    sub.add_parser("book-done", help="§26.4 book-level gate").set_defaults(fn=cmd_book_done)
    s = sub.add_parser("next-book", help="advance active book and re-init state")
    s.add_argument("--no-archive", dest="archive", action="store_false")
    s.set_defaults(fn=cmd_next_book)

    s = sub.add_parser("calibrate", help="measure voice bands from sample chapters")
    s.add_argument("files", nargs="+"); s.add_argument("--pov"); s.add_argument("--write", action="store_true")
    s.set_defaults(fn=cmd_calibrate)

    s = sub.add_parser("export", help="assemble the manuscript")
    s.add_argument("--docx", action="store_true")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("models", help="search OpenRouter models")
    s.add_argument("query", nargs="?")
    s.set_defaults(fn=cmd_models)

    a = ap.parse_args(argv)
    try:
        a.fn(a)
    except IntakeError as e:
        print("\n*** SERIES-BIBLE INTAKE HARD STOP ***")
        print(e)
        print("No chapter may be written or validated without intaking the series bible. Refusing to run.")
        sys.exit(2)


if __name__ == "__main__":
    main()
