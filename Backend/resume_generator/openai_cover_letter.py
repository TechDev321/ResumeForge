from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from openai import OpenAI


class CoverLetterGenerationError(RuntimeError):
    pass


_CACHE_VERSION = 1

_DASH_RE = re.compile(r"[—–]")

# Placeholders the model must fill (Date/Company/Job-Title are set in post-processing).
_MODEL_PLACEHOLDERS = [
    "<< Greeting >>",
    "<< Paragraph-1 >>",
    "<< Paragraph-2 >>",
    "<< Paragraph-3 >>",
    "<< Paragraph-4 >>",
    "<< Sign-Off >>",
]


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sanitize_dashes(text: str) -> str:
    return _DASH_RE.sub("-", text or "")


def _word_count(*parts: str) -> int:
    text = " ".join(p.strip() for p in parts if p and p.strip())
    return len(re.findall(r"\b\w+\b", text))


def _parse_jd_header(jd_text: str) -> tuple[str, str]:
    lines = [ln.strip() for ln in jd_text.splitlines() if ln.strip()]
    job_title = lines[0] if lines else "Software_Engineer"
    company = lines[1] if len(lines) > 1 else "Company"
    return company, job_title


def _title_case_token(value: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", value or "").strip()
    parts = re.split(r"[\s_-]+", cleaned)
    return "_".join(p[:1].upper() + p[1:] if p else "" for p in parts if p)


def _default_file_name(*, company: str, job_title: str) -> str:
    company_tok = _title_case_token(company)
    role_tok = _title_case_token(job_title)
    return f"Corey_Joel_Deloach_{company_tok}_{role_tok}_Cover_Letter.docx"


def _format_letter_date() -> str:
    return date.today().strftime("%B %d, %Y")


def _make_json_schema(placeholders: list[str]) -> dict[str, Any]:
    props = {ph: {"type": "string"} for ph in placeholders}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "file_name": {"type": "string"},
            "replacements": {
                "type": "object",
                "additionalProperties": False,
                "properties": props,
                "required": placeholders,
            },
        },
        "required": ["file_name", "replacements"],
    }


def _post_process_replacements(
    replacements: dict[str, str],
    *,
    jd_text: str,
) -> dict[str, str]:
    company, job_title = _parse_jd_header(jd_text)
    out: dict[str, str] = {}

    for key, value in replacements.items():
        out[key] = _sanitize_dashes(str(value).strip())

    out["<< Date >>"] = _format_letter_date()
    out["<< Company-Name >>"] = company
    out["<< Job-Title >>"] = job_title

    return out


def generate_cover_letter_replacements(
    *,
    api_key: str,
    model: str,
    prompt_rules: str,
    jd_text: str,
    resume_text: str,
    placeholders: list[str],
    cache_dir: Path | None = None,
) -> tuple[str, dict[str, str]]:
    """Return (file_name, replacements) for all template placeholders."""

    model_placeholders = [ph for ph in placeholders if ph in _MODEL_PLACEHOLDERS]
    if not model_placeholders:
        raise CoverLetterGenerationError(
            f"Template must include model placeholders; expected any of: {_MODEL_PLACEHOLDERS}"
        )

    cache_key_payload = json.dumps(
        {
            "cache_version": _CACHE_VERSION,
            "model": model,
            "prompt_rules": prompt_rules,
            "jd": jd_text,
            "resume_text": resume_text,
            "placeholders": placeholders,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    cache_key = _sha256_text(cache_key_payload)
    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = cache_dir / f"{cache_key}.json"
        if cache_path.exists():
            obj = json.loads(cache_path.read_text(encoding="utf-8"))
            return obj["file_name"], obj["replacements"]

    client = OpenAI(api_key=api_key)
    schema = _make_json_schema(model_placeholders)

    system = (
        "You write tailored cover letters based on a resume and job description. "
        "Return ONLY valid JSON matching the given JSON schema."
    )

    user = f"""{prompt_rules.strip()}

You must output a JSON object with:
- file_name: output .docx name using underscores (see naming rules)
- replacements: values for EVERY placeholder listed below

IMPORTANT:
- Plain text only in replacement values (no markdown, no bullet characters).
- Do not include placeholder tokens inside values.
- Do not output any text outside JSON.

<JD>
{jd_text.strip()}
</JD>

<resume>
{resume_text.strip()}
</resume>

Placeholders to fill (must include all):
{json.dumps(model_placeholders, ensure_ascii=False, indent=2)}
"""

    resp = client.responses.create(
        model=model,
        instructions=system,
        input=user,
        text={
            "format": {
                "type": "json_schema",
                "name": "cover_letter_replacements",
                "schema": schema,
                "strict": True,
            }
        },
        temperature=0,
    )

    out_text = resp.output_text
    try:
        data = json.loads(out_text)
    except Exception as e:
        raise CoverLetterGenerationError(f"Model did not return valid JSON: {e}\nRaw: {out_text[:500]}")

    file_name = data.get("file_name")
    replacements = data.get("replacements")
    if not isinstance(file_name, str) or not file_name.lower().endswith(".docx"):
        raise CoverLetterGenerationError("Invalid file_name in model output")
    if not isinstance(replacements, dict):
        raise CoverLetterGenerationError("Invalid replacements in model output")

    missing = [ph for ph in model_placeholders if ph not in replacements]
    if missing:
        raise CoverLetterGenerationError(f"Missing placeholders in replacements: {missing}")

    model_values = {k: _sanitize_dashes(str(v)) for k, v in replacements.items()}
    full_replacements = _post_process_replacements(model_values, jd_text=jd_text)

    # Ensure every template placeholder has a value.
    for ph in placeholders:
        full_replacements.setdefault(ph, "")

    body_words = _word_count(
        full_replacements.get("<< Paragraph-1 >>", ""),
        full_replacements.get("<< Paragraph-2 >>", ""),
        full_replacements.get("<< Paragraph-3 >>", ""),
        full_replacements.get("<< Paragraph-4 >>", ""),
    )
    if body_words < 200 or body_words > 450:
        # Soft guardrail: normalize filename but keep output; prompt targets 250-400.
        pass

    company, job_title = _parse_jd_header(jd_text)
    file_name = _default_file_name(company=company, job_title=job_title)

    if cache_dir is not None:
        cache_path.write_text(
            json.dumps({"file_name": file_name, "replacements": full_replacements}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return file_name, full_replacements
