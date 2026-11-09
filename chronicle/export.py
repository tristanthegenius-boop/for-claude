"""Manuscript export (Markdown and .docx)."""
from __future__ import annotations

import re
from pathlib import Path

from .project import Project


def chapters(pj: Project) -> list[tuple[int, str]]:
    st = pj.state()
    return [(c, pj.chapter_path(c).read_text(encoding="utf-8")) for c in st["committed_chapters"]
            if pj.chapter_path(c).exists()]


def export_markdown(pj: Project, out: Path) -> Path:
    title = pj.state()["meta"].get("title", "Manuscript")
    body = "\n\n".join(t.strip() for _, t in chapters(pj))
    out.write_text(f"% {title}\n\n{body}\n", encoding="utf-8")
    return out


def export_docx(pj: Project, out: Path) -> Path:
    from docx import Document
    from docx.enum.text import WD_BREAK

    doc = Document()
    doc.add_heading(pj.state()["meta"].get("title", "Manuscript"), 0)
    for i, (_, text) in enumerate(chapters(pj)):
        if i:
            doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
        for block in re.split(r"\n\s*\n", text.strip()):
            b = block.strip()
            if b.startswith("# "):
                doc.add_heading(b[2:].strip(), 1)
            elif b.startswith("### "):
                doc.add_heading(b[4:].strip().strip("*"), 2)
            elif b.startswith("**POV"):
                continue
            else:
                doc.add_paragraph(" ".join(b.splitlines()))
    doc.save(out)
    return out
