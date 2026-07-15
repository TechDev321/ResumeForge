from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, List, Tuple

from .docx_utils import (
    iter_paragraphs,
    parse_bold_markdown,
    replace_paragraph_text_with_runs,
    replace_placeholders_in_paragraph_runs,
)


PLACEHOLDER_RE = re.compile(r"<<\s*[^<>]+?\s*>>")


def find_placeholders_in_text(text: str) -> list[str]:
    return PLACEHOLDER_RE.findall(text or "")


def extract_placeholders(document) -> list[str]:
    """Return distinct placeholders found in the template docx."""

    found: list[str] = []
    seen = set()
    for p in iter_paragraphs(document):
        for ph in find_placeholders_in_text(p.text):
            if ph not in seen:
                found.append(ph)
                seen.add(ph)
    return found


def replace_in_paragraph(paragraph, replacements: Dict[str, str]) -> None:
    """Replace placeholders in a paragraph, preserving paragraph formatting.

    Assumptions/constraints:
    - Placeholders exist wholly within paragraph text.
    - We rewrite the paragraph runs completely, but keep paragraph-level formatting.
    - Replacements may contain **bold** markers.
    """

    # Fast path: nothing to do
    if not paragraph.text or "<<" not in (paragraph.text or ""):
        return

    # Preferred path: do a run-aware replacement so we keep *all* existing
    # run-level formatting outside placeholders.
    if replace_placeholders_in_paragraph_runs(paragraph, replacements):
        return

    # Fallback: placeholder tokens can be split across multiple runs in some
    # Word documents; in that case we still do the old paragraph-wide rebuild.
    text = paragraph.text
    for key, val in replacements.items():
        if key in text:
            text = text.replace(key, val)

    runs = parse_bold_markdown(text)
    replace_paragraph_text_with_runs(paragraph, runs)


def apply_replacements(document, replacements: Dict[str, str]) -> None:
    for p in iter_paragraphs(document):
        replace_in_paragraph(p, replacements)

    # No post-pass cleanup needed currently; company redaction uses a non-empty
    # placeholder value (e.g., "Undisclosed").

    # Post-pass: remove the Machine Learning skill line if it's effectively empty.
    # This keeps the Skills section job-relevant when the role isn't ML-focused.
    for p in list(iter_paragraphs(document)):
        t = (p.text or "").strip()
        if not t.lower().startswith("machine learning:"):
            continue

        _, rest = t.split(":", 1)
        rest = rest.strip().lower()
        if rest in {"", "n/a", "na", "none", "none applicable", "not applicable"}:
            try:
                p._element.getparent().remove(p._element)
            except Exception:
                # If we fail to remove, at least blank it.
                replace_paragraph_text_with_runs(p, parse_bold_markdown(""))
