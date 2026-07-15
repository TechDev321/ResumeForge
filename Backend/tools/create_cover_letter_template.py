"""Create the default cover letter DOCX template with placeholders."""

from __future__ import annotations

from pathlib import Path

from resume_generator.cover_letter_template import create_cover_letter_template


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    out = root / "corey_cover_letter_template.docx"
    create_cover_letter_template(out)
    print(f"Wrote {out}")
