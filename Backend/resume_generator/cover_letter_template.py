from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.shared import Pt


def create_cover_letter_template(path: Path) -> None:
    doc = Document()
    normal = doc.styles["Normal"]
    normal.font.name = "Arial"
    normal.font.size = Pt(11)

    lines = [
        "Corey Joel Deloach",
        "Coreydejoel@outlook.com | (407)-778-8227 | Ladson, SC 29456-5264",
        "",
        "<< Date >>",
        "",
        "Hiring Manager",
        "<< Company-Name >>",
        "",
        "Re: << Job-Title >>",
        "",
        "<< Greeting >>",
        "",
        "<< Paragraph-1 >>",
        "",
        "<< Paragraph-2 >>",
        "",
        "<< Paragraph-3 >>",
        "",
        "<< Paragraph-4 >>",
        "",
        "<< Sign-Off >>",
        "Corey Joel Deloach",
    ]

    for line in lines:
        doc.add_paragraph(line)

    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path))
