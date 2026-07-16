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


# Bump when naming / post-processing rules change so old caches are ignored.
_CACHE_VERSION = 2

_DASH_RE = re.compile(r"[—–]")

# Placeholders the model must fill (Date/Company/Job-Title may be set in post-processing).
_MODEL_PLACEHOLDERS = [
    "<< Greeting >>",
    "<< Paragraph-1 >>",
    "<< Paragraph-2 >>",
    "<< Paragraph-3 >>",
    "<< Paragraph-4 >>",
    "<< Sign-Off >>",
]

_GENERIC_LABELS = {
    "company",
    "company name",
    "company_name",
    "the company",
    "employer",
    "organization",
    "job description",
    "job_description",
    "job title",
    "job_title",
    "target role",
    "target_role",
    "role",
    "position",
    "title",
    "about the role",
    "about us",
    "overview",
    "description",
    "hiring manager",
}

_WEAK_FILE_STEMS = {
    "cover_letter",
    "coverletter",
    "letter",
    "output",
    "generated",
    "document",
    "file",
    "result",
}


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sanitize_dashes(text: str) -> str:
    return _DASH_RE.sub("-", text or "")


def _word_count(*parts: str) -> int:
    text = " ".join(p.strip() for p in parts if p and p.strip())
    return len(re.findall(r"\b\w+\b", text))


def _normalize_label(value: str) -> str:
    return re.sub(r"[\s_-]+", " ", (value or "").strip().lower())


def _is_generic_label(value: str) -> bool:
    cleaned = _normalize_label(value)
    if not cleaned or len(cleaned) < 2:
        return True
    if cleaned in _GENERIC_LABELS:
        return True
    # Common pasted headings like "Job Description:" / "Company:"
    if cleaned.rstrip(":") in _GENERIC_LABELS:
        return True
    return False


def _title_case_token(value: str) -> str:
    cleaned = re.sub(r"[^\w\s-]", "", value or "").strip()
    parts = re.split(r"[\s_-]+", cleaned)
    return "_".join(p[:1].upper() + p[1:] if p else "" for p in parts if p)


def _parse_jd_header(jd_text: str) -> tuple[str, str]:
    """Best-effort (company, job_title) from early JD lines, skipping generic labels."""

    lines = [ln.strip() for ln in (jd_text or "").splitlines() if ln.strip()]
    useful = [ln for ln in lines if not _is_generic_label(ln)]

    # Prefer first two useful lines: often Role then Company, or Company then Role.
    # Heuristic: shorter early line that looks like a role title is job_title.
    if len(useful) >= 2:
        a, b = useful[0], useful[1]
        # If line looks like "Company: Acme" / "Role: Engineer", strip labels.
        a = _strip_field_prefix(a)
        b = _strip_field_prefix(b)
        if _looks_like_role(a) and not _looks_like_role(b):
            return b, a
        if _looks_like_role(b) and not _looks_like_role(a):
            return a, b
        # Default assumption matching prior behavior: line1=role, line2=company
        return b, a

    if len(useful) == 1:
        only = _strip_field_prefix(useful[0])
        if _looks_like_role(only):
            return "Company", only
        return only, "Software_Engineer"

    return "Company", "Software_Engineer"


_FIELD_PREFIX_RE = re.compile(
    r"^(?:company(?:\s*name)?|employer|organization|job\s*title|title|role|position)\s*[:\-]\s*",
    re.IGNORECASE,
)


def _strip_field_prefix(value: str) -> str:
    return _FIELD_PREFIX_RE.sub("", (value or "").strip()).strip()


def _looks_like_role(value: str) -> bool:
    text = (value or "").strip()
    if not text or _is_generic_label(text):
        return False
    # Typical role cues.
    if re.search(
        r"\b(engineer|developer|manager|director|architect|analyst|designer|"
        r"specialist|lead|scientist|consultant|intern)\b",
        text,
        re.IGNORECASE,
    ):
        return True
    # Short title-like phrases without URLs / long sentences.
    if len(text) <= 60 and "http" not in text.lower() and text.count(" ") <= 8:
        if not re.search(r"[.!?]$", text):
            return True
    return False


def _default_file_name(*, company: str, job_title: str) -> str:
    company_tok = _title_case_token(company) or "Company"
    role_tok = _title_case_token(job_title) or "Software_Engineer"
    return f"Corey_Joel_Deloach_{company_tok}_{role_tok}_Cover_Letter.docx"


def _format_letter_date() -> str:
    return date.today().strftime("%B %d, %Y")


def _is_weak_cover_file_name(file_name: str) -> bool:
    fn = (file_name or "").strip()
    if not fn.lower().endswith(".docx"):
        return True
    stem = Path(fn).stem
    if stem.lower() in _WEAK_FILE_STEMS:
        return True
    if "cover_letter" not in stem.lower():
        return True

    parts = [p for p in stem.split("_") if p]
    # Expect Person parts + Company + Role + Cover + Letter (at least 5 tokens).
    if len(parts) < 5:
        return True

    joined = " ".join(parts).lower()
    if "company name" in joined or "job description" in joined:
        return True
    if any(_is_generic_label(p) for p in parts if p.lower() not in {"cover", "letter", "corey", "joel", "deloach"}):
        # Only flag if both company-ish and role-ish slots look generic via full stem check
        pass

    # Explicit generic tokens that caused the reported bug.
    lower_parts = {p.lower() for p in parts}
    if "company" in lower_parts and "name" in lower_parts:
        return True
    if "job" in lower_parts and "description" in lower_parts:
        return True

    return False


def _company_role_from_file_name(file_name: str) -> tuple[str | None, str | None]:
    """Try to recover (company, role) from a good Cover_Letter filename."""

    stem = Path((file_name or "").strip()).stem
    parts = [p for p in stem.split("_") if p]
    if len(parts) < 5:
        return None, None
    if [p.lower() for p in parts[-2:]] != ["cover", "letter"]:
        return None, None

    body = parts[:-2]
    # Drop leading person name tokens when recognizable.
    if len(body) >= 3 and body[0].lower() == "corey":
        if body[1].lower() == "joel" and body[2].lower() == "deloach":
            body = body[3:]
        elif body[1].lower() == "deloach":
            body = body[2:]

    if len(body) < 2:
        return None, None

    # Split body into company + role: prefer a role-looking suffix.
    for i in range(1, len(body)):
        company = " ".join(body[:i])
        role = " ".join(body[i:])
        if _looks_like_role(role) and not _is_generic_label(company):
            return company, role

    return " ".join(body[:1]), " ".join(body[1:])


def _resolve_company_role(
    *,
    jd_text: str,
    model_company: str | None,
    model_job_title: str | None,
    model_file_name: str,
) -> tuple[str, str]:
    company = (model_company or "").strip()
    job_title = (model_job_title or "").strip()

    if company and job_title and not _is_generic_label(company) and not _is_generic_label(job_title):
        return company, job_title

    parsed_company, parsed_role = _parse_jd_header(jd_text)
    if (not company or _is_generic_label(company)) and parsed_company and not _is_generic_label(parsed_company):
        company = parsed_company
    if (not job_title or _is_generic_label(job_title)) and parsed_role and not _is_generic_label(parsed_role):
        job_title = parsed_role

    if (not company or _is_generic_label(company)) or (not job_title or _is_generic_label(job_title)):
        from_fn_company, from_fn_role = _company_role_from_file_name(model_file_name)
        if (not company or _is_generic_label(company)) and from_fn_company and not _is_generic_label(from_fn_company):
            company = from_fn_company
        if (not job_title or _is_generic_label(job_title)) and from_fn_role and not _is_generic_label(from_fn_role):
            job_title = from_fn_role

    if not company or _is_generic_label(company):
        company = "Company"
    if not job_title or _is_generic_label(job_title):
        job_title = "Software_Engineer"

    return company, job_title


def _ensure_cover_file_name(
    file_name: str,
    *,
    company: str,
    job_title: str,
) -> str:
    fn = (file_name or "").strip()
    if not _is_weak_cover_file_name(fn):
        stem = Path(fn).stem
        parts = [p for p in re.split(r"[\s_]+", stem) if p]
        safe = "_".join(_title_case_token(p) or p for p in parts)
        if "cover_letter" not in safe.lower():
            safe = f"{safe}_Cover_Letter"
        return f"{safe}.docx"

    return _default_file_name(company=company, job_title=job_title)


def _make_json_schema(placeholders: list[str]) -> dict[str, Any]:
    props = {ph: {"type": "string"} for ph in placeholders}
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "file_name": {"type": "string"},
            "company_name": {"type": "string"},
            "job_title": {"type": "string"},
            "replacements": {
                "type": "object",
                "additionalProperties": False,
                "properties": props,
                "required": placeholders,
            },
        },
        "required": ["file_name", "company_name", "job_title", "replacements"],
    }


def _post_process_replacements(
    replacements: dict[str, str],
    *,
    company: str,
    job_title: str,
) -> dict[str, str]:
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
- file_name: FirstName_LastName_CompanyName_TargetRole_Cover_Letter.docx (underscores only)
- company_name: the real hiring company from the JD (NOT labels like "Company Name")
- job_title: the real target role from the JD (NOT labels like "Job Description")
- replacements: values for EVERY placeholder listed below

IMPORTANT:
- Extract company_name and job_title from the actual JD content.
- Never use placeholder/generic labels such as "Company Name", "Job Description", "Job Title".
- file_name MUST include the real company and role tokens, e.g. Corey_Deloach_Microsoft_Senior_AI_Engineer_Cover_Letter.docx
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
    company_name = data.get("company_name")
    job_title = data.get("job_title")
    replacements = data.get("replacements")
    if not isinstance(file_name, str) or not file_name.lower().endswith(".docx"):
        raise CoverLetterGenerationError("Invalid file_name in model output")
    if not isinstance(replacements, dict):
        raise CoverLetterGenerationError("Invalid replacements in model output")

    missing = [ph for ph in model_placeholders if ph not in replacements]
    if missing:
        raise CoverLetterGenerationError(f"Missing placeholders in replacements: {missing}")

    company, role = _resolve_company_role(
        jd_text=jd_text,
        model_company=company_name if isinstance(company_name, str) else None,
        model_job_title=job_title if isinstance(job_title, str) else None,
        model_file_name=file_name,
    )

    model_values = {k: _sanitize_dashes(str(v)) for k, v in replacements.items()}
    full_replacements = _post_process_replacements(
        model_values,
        company=company,
        job_title=role,
    )

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
        # Soft guardrail: keep output; prompt targets 250-400.
        pass

    file_name = _ensure_cover_file_name(file_name, company=company, job_title=role)

    if cache_dir is not None:
        cache_path.write_text(
            json.dumps({"file_name": file_name, "replacements": full_replacements}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return file_name, full_replacements
