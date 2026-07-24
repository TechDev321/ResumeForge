from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from openai import OpenAI


class ResumeGenerationError(RuntimeError):
    pass


# Increment this when post-processing rules change in ways that should invalidate
# old cached generations.
_CACHE_VERSION = 10


_EARLY_ROLE_SENIORITY_BANNED_RE = re.compile(
    r"\b(Senior|Sr\.?|Lead|Staff|Principal|Architect|Manager|Director|Head|VP|Chief)\b",
    re.IGNORECASE,
)

_ROLE_TITLE_DATE_RE = re.compile(
    r"(\b\d{2}/\d{4}\b\s*[-—–]\s*\b\d{2}/\d{4}\b|\b\d{4}\b\s*[-—–]\s*\b\d{4}\b)",
    re.IGNORECASE,
)

_ROLE_TITLE_SPLIT_RE = re.compile(r"\s*(?:\r?\n|;)+\s*")

# Guardrail: prevent apparent seniority regressions in the timeline.
#
# If a newer role title includes entry-level modifiers (e.g., "Junior") while an
# older role is already at a higher level (e.g., plain "Software Engineer"),
# recruiters will read it as a demotion. Since these titles are LLM-generated to
# match the JD, we defensively normalize them to preserve a natural progression.
_LOW_SENIORITY_WORD_RE = re.compile(r"\b(Junior|Associate|Entry[- ]Level|Intern)\b", re.IGNORECASE)
_LOW_SENIORITY_LEADING_RE = re.compile(r"^(Junior|Associate|Entry[- ]Level|Intern)\s+", re.IGNORECASE)

# Header line in the DOCX template:
#   << Main-Title >> | << Sub-Title-1 >> · << Sub-Title-2 >> · … · << Sub-Title-5 >>
# Main-Title = role only; Sub-Title-* = one stack keyword each.
_MAIN_TITLE_SEP_RE = re.compile(r"\s*[|—–]\s*")
_MAIN_TITLE_KW_SPLIT_RE = re.compile(r"\s*[·•|,]\s*")
_MAIN_TITLE_MAX_KEYWORDS = 5
_SUB_TITLE_KEYS = tuple(f"<< Sub-Title-{i} >>" for i in range(1, _MAIN_TITLE_MAX_KEYWORDS + 1))
_JOB_TITLE_LIKE_RE = re.compile(
    r"\b(Engineer|Developer|Analyst|Specialist|Coordinator|Architect|Manager|Programmer)\b",
    re.IGNORECASE,
)
_MAIN_TITLE_ABSTRACT_RE = re.compile(
    r"""^(?:
        architecture|architectures|
        scalable\s+systems?|
        leadership|
        collaboration|
        troubleshooting|
        code\s+quality|
        cross[- ]functional\s+teams?|
        software\s+development\s+lifecycle|sdlc|
        performance\s+optimization|
        security\s+practices?|
        continuous\s+delivery|
        continuous\s+integration|
        backend\s+development|
        frontend\s+development|
        full[- ]?stack\s+development|
        microservices?|
        event[- ]driven\s+systems?|
        cloud\s+integration|
        automated\s+testing|
        rest\s+apis?|
        api\s+design|
        agile|
        ci/?cd|
        devops
    )$""",
    re.IGNORECASE | re.VERBOSE,
)


def _has_low_seniority_modifier(title: str) -> bool:
    return bool(_LOW_SENIORITY_WORD_RE.search(title or ""))


def _strip_low_seniority_modifier(title: str) -> str:
    t = (title or "").strip()
    if not t:
        return t

    # Prefer removing only a leading modifier ("Junior Software Engineer"), but
    # also remove stray standalone words if the model outputs something like
    # "Software Engineer (Junior)" (parentheses are usually removed earlier).
    t = _LOW_SENIORITY_LEADING_RE.sub("", t).strip()
    t = _LOW_SENIORITY_WORD_RE.sub("", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" -|,\t")
    return t


def _sanitize_title_progression(replacements: dict[str, str]) -> dict[str, str]:
    """Prevent newer titles from being labeled Junior/Associate after mid-level roles.

    This is intentionally minimal: we only strip *low* seniority modifiers from
    newer roles when an older role is already non-low.
    """

    out = dict(replacements)

    t1 = out.get("<< Role-Title-1 >>", "") or ""
    t2 = out.get("<< Role-Title-2 >>", "") or ""
    t3 = out.get("<< Role-Title-3 >>", "") or ""

    if isinstance(t1, str) and _has_low_seniority_modifier(t1):
        older_titles = [t for t in (t2, t3) if isinstance(t, str) and t.strip()]
        if any(not _has_low_seniority_modifier(t) for t in older_titles):
            cleaned = _strip_low_seniority_modifier(t1)
            if cleaned:
                out["<< Role-Title-1 >>"] = cleaned

    # Keep the headline consistent with the most recent role.
    main = out.get("<< Main-Title >>")
    if isinstance(main, str) and main.strip() and _has_low_seniority_modifier(main):
        # If Role-Title-1 is now non-low, strip low modifiers from the main title.
        rt1 = out.get("<< Role-Title-1 >>", "") or ""
        if isinstance(rt1, str) and rt1.strip() and (not _has_low_seniority_modifier(rt1)):
            cleaned_main = _strip_low_seniority_modifier(main)
            if cleaned_main:
                out["<< Main-Title >>"] = cleaned_main

    # If the summary starts with "Junior ...", normalize to avoid drawing
    # attention to a perceived demotion.
    summary_key = "<< Summary/Profile >>"
    summary = out.get(summary_key)
    if isinstance(summary, str) and summary.strip():
        # Only adjust the opening phrase; leave the rest of the summary intact.
        summary = re.sub(
            r"^\s*(Junior|Associate|Entry[- ]Level)\s+(software\s+engineer)",
            r"\2",
            summary,
            flags=re.IGNORECASE,
        )
        out[summary_key] = summary.strip()

    return out


def _normalize_file_name_seniority(file_name: str, *, replacements: dict[str, str]) -> str:
    """If we stripped low seniority from the resume, avoid "_Junior_" in the file name."""

    fn = (file_name or "").strip()
    if not fn.lower().endswith(".docx"):
        return fn

    main = replacements.get("<< Main-Title >>", "") or ""
    rt1 = replacements.get("<< Role-Title-1 >>", "") or ""
    if _has_low_seniority_modifier(main) or _has_low_seniority_modifier(rt1):
        return fn

    # Remove role-level tokens that no longer match the resume content.
    fn = re.sub(r"_+(Junior|Associate|Entry[- ]Level|Intern)_+", "_", fn, flags=re.IGNORECASE)
    fn = re.sub(r"_+(Junior|Associate|Entry[- ]Level|Intern)(?=\.docx$)", "", fn, flags=re.IGNORECASE)
    fn = re.sub(r"_{2,}", "_", fn).replace("_.docx", ".docx")
    return fn


def _title_case_token(value: str) -> str:
    cleaned = re.sub(r"\*+", "", value or "")
    cleaned = re.sub(r"[^\w\s\-]", "", cleaned).strip()
    parts = re.split(r"[\s_\-]+", cleaned)
    return "_".join((p[:1].upper() + p[1:]) for p in parts if p)


def _parse_jd_header(jd_text: str) -> tuple[str, str]:
    """Best-effort (company, job_title) from the first two non-empty JD lines."""

    lines = [ln.strip() for ln in (jd_text or "").splitlines() if ln.strip()]
    job_title = lines[0] if lines else "Software_Engineer"
    company = lines[1] if len(lines) > 1 else "Company"
    return company, job_title


def _person_name_token(*, resume_template_text: str) -> str:
    """Person name for filenames: env override, else first name-like line in the template."""

    env_name = (os.getenv("RESUME_PERSON_NAME") or "").strip()
    if env_name:
        return _title_case_token(env_name)

    for ln in (resume_template_text or "").splitlines():
        text = ln.strip()
        if not text or "<<" in text:
            continue
        # Skip obvious section headers / contact lines.
        if "@" in text or "http" in text.lower() or re.search(r"\d{3}", text):
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z\s.\-]{1,60}", text):
            return _title_case_token(text)

    return "Candidate"


_WEAK_FILE_STEMS = {
    "resume",
    "output",
    "generated",
    "document",
    "file",
    "result",
}


def _is_weak_file_name(file_name: str) -> bool:
    fn = (file_name or "").strip()
    if not fn.lower().endswith(".docx"):
        return True
    stem = Path(fn).stem
    if stem.lower() in _WEAK_FILE_STEMS:
        return True
    # Expect at least First_Last_Company_Role (4+ underscore parts).
    parts = [p for p in stem.split("_") if p]
    return len(parts) < 4


def _ensure_file_name(
    file_name: str,
    *,
    jd_text: str,
    resume_template_text: str,
    replacements: dict[str, str],
) -> str:
    """Guarantee FirstName_LastName_CompanyName_TargetRole.docx."""

    fn = (file_name or "").strip()
    if not _is_weak_file_name(fn):
        return fn if fn.lower().endswith(".docx") else f"{fn}.docx"

    company, job_from_jd = _parse_jd_header(jd_text)
    role = replacements.get("<< Main-Title >>") or replacements.get("<< Role-Title-1 >>") or job_from_jd
    if isinstance(role, str):
        # Filename should use the role only, not the keyword suffix.
        role = _MAIN_TITLE_SEP_RE.split(role, maxsplit=1)[0].strip() or role
    person = _person_name_token(resume_template_text=resume_template_text)

    built = f"{person}_{_title_case_token(company)}_{_title_case_token(role)}.docx"
    return re.sub(r"_{2,}", "_", built)

"""OpenAI resume generator.

This project uses `corey_resume_template_OpenAI.docx` where employer/company
names are *literal text* in the template (NOT placeholders).

Policy (user request):
- Do not attempt to infer “peer/similar companies”
- Do not attempt to parse/extract a target company from the JD
- Do not attempt to normalize/replace any company names

We only fill the <<...>> placeholders and leave the template's real company
names untouched.
"""


def _normalize_role_titles_one_per_slot(replacements: dict[str, str]) -> dict[str, str]:
    """Ensure each Role-Title-* value is a single job title for that slot.

    Sometimes the model may try to represent an internal promotion at the same
    company by outputting multiple titles or multiple lines inside one
    placeholder. The template already fixes companies/dates, so we normalize the
    title fields to prevent "split history" output.
    """

    out = dict(replacements)
    for k, v in replacements.items():
        if "Role-Title-" not in k:
            continue
        if not isinstance(v, str):
            continue

        title = v.strip()
        if not title:
            continue

        # Keep only the first segment if multiple titles are provided.
        title = _ROLE_TITLE_SPLIT_RE.split(title, maxsplit=1)[0].strip()

        # Remove any appended date ranges if the model violated the rule.
        m = _ROLE_TITLE_DATE_RE.search(title)
        if m:
            title = title[: m.start()].strip()

        # Remove any appended location/company bits separated by pipes.
        if "|" in title:
            title = title.split("|", 1)[0].strip()

        # Normalize whitespace and trailing separators.
        title = re.sub(r"\s{2,}", " ", title).strip(" -|,\t")

        out[k] = title

    return out


def _is_main_title_abstract_keyword(token: str) -> bool:
    t = (token or "").strip()
    if not t:
        return True
    if _MAIN_TITLE_ABSTRACT_RE.match(t):
        return True
    # Alternate job titles belong in Role-Title-*, never in header keywords.
    if _JOB_TITLE_LIKE_RE.search(t):
        return True
    # Multi-word soft phrases the model often dumps after • separators.
    words = t.lower().split()
    if len(words) >= 2 and words[-1] in {
        "development",
        "systems",
        "practices",
        "optimization",
        "lifecycle",
        "teams",
        "delivery",
        "integration",
        "testing",
        "quality",
        "design",
        "collaboration",
        "troubleshooting",
    }:
        # Allow real stack names like "ASP.NET Core" / "SQL Server" (tools/platforms).
        stackish = {"net", "asp.net", "sql", "server", "core", "node.js", "next.js", "spring"}
        if not any(w.strip(".#") in stackish or "." in w for w in words):
            return True
    return False


def _extract_header_role_and_keywords(text: str) -> tuple[str, list[str]]:
    """Parse a possibly packed header string into (role, keywords)."""

    text = (text or "").replace("**", "").strip()
    text = re.sub(r"\s{2,}", " ", text)
    if not text:
        return "", []

    parts = _MAIN_TITLE_SEP_RE.split(text, maxsplit=1)
    role = (parts[0] or "").strip(" -|,\t")
    kw_blob = parts[1].strip() if len(parts) > 1 else ""

    # Cut off any secondary role/keyword dump after another dash/pipe inside kw_blob.
    if kw_blob:
        kw_blob = _MAIN_TITLE_SEP_RE.split(kw_blob, maxsplit=1)[0].strip()

    if not kw_blob and "·" in text:
        bits = [b.strip() for b in text.split("·") if b.strip()]
        if bits and _JOB_TITLE_LIKE_RE.search(bits[0]):
            role, kw_blob = bits[0], " · ".join(bits[1:])

    keywords: list[str] = []
    seen: set[str] = set()
    for tok in _MAIN_TITLE_KW_SPLIT_RE.split(kw_blob):
        tok = tok.strip(" -|,\t.()[]")
        if not tok or _is_main_title_abstract_keyword(tok):
            continue
        low = tok.lower()
        if low in seen:
            continue
        seen.add(low)
        keywords.append(tok)

    return role, keywords


def _normalize_main_title(replacements: dict[str, str]) -> dict[str, str]:
    """Clamp header placeholders to: Role | k1 · k2 · k3 · k4 · k5.

    The DOCX template already joins:
      << Main-Title >> | << Sub-Title-1 >> · … · << Sub-Title-5 >>
    so Main-Title must be role-only and each Sub-Title a single stack keyword.
    """

    out = dict(replacements)
    main_key = "<< Main-Title >>"
    raw_main = out.get(main_key, "")
    role, from_main = _extract_header_role_and_keywords(raw_main if isinstance(raw_main, str) else "")

    if not role:
        fallback = out.get("<< Role-Title-1 >>")
        role = fallback.strip() if isinstance(fallback, str) and fallback.strip() else "Software Engineer"

    # Prefer keywords packed into Main-Title, then any usable Sub-Title values.
    candidates: list[str] = list(from_main)
    seen = {k.lower() for k in candidates}
    for sk in _SUB_TITLE_KEYS:
        val = out.get(sk, "")
        if not isinstance(val, str):
            continue
        # Sub-titles should be single tokens; still split if the model stuffed several in.
        for tok in _MAIN_TITLE_KW_SPLIT_RE.split(val.replace("**", "").strip()) or [val]:
            tok = tok.strip(" -|,\t.()[]")
            if not tok or _is_main_title_abstract_keyword(tok):
                continue
            low = tok.lower()
            if low in seen:
                continue
            seen.add(low)
            candidates.append(tok)

    keywords = candidates[:_MAIN_TITLE_MAX_KEYWORDS]

    out[main_key] = role
    for i, sk in enumerate(_SUB_TITLE_KEYS):
        out[sk] = keywords[i] if i < len(keywords) else ""

    return out


def _sanitize_earliest_role_title(replacements: dict[str, str]) -> dict[str, str]:
    """Guardrail: ensure earliest role title isn't senior-level.

    The prompts should already enforce this, but this adds a small safety-net so
    bad generations don't slip through when prompts/models change.

    This is intentionally conservative: if we detect banned seniority words in
    Role-Title-4, we remove them and normalize whitespace.
    """

    key = "<< Role-Title-4 >>"
    title = replacements.get(key)
    if not isinstance(title, str) or not title.strip():
        return replacements

    if not _EARLY_ROLE_SENIORITY_BANNED_RE.search(title):
        return replacements

    cleaned = _EARLY_ROLE_SENIORITY_BANNED_RE.sub("", title)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" -|,\t")
    if not cleaned:
        # fallback to a neutral title if we stripped everything
        cleaned = "Engineer"

    out = dict(replacements)
    out[key] = cleaned
    return out


def _count_bold_markers(text: str) -> int:
    return text.count("**") // 2


def _needs_bold_enforcement(replacements: dict[str, str]) -> bool:
    """Heuristic: require at least 1 bold span in summary and at least 1 in most bullets.

    We keep this simple to avoid overfitting to a single template.
    """

    summary = replacements.get("<< Summary/Profile >>", "")
    if _count_bold_markers(summary) < 2:
        return True

    bullet_keys = [k for k in replacements.keys() if "Role-Description-" in k]
    if not bullet_keys:
        return False

    low = 0
    for k in bullet_keys:
        if _count_bold_markers(replacements.get(k, "")) < 1:
            low += 1

    # If 25%+ of bullets have no bold spans, enforce.
    return (low / max(1, len(bullet_keys))) >= 0.25


def enforce_bolding(
    *,
    client: OpenAI,
    model: str,
    replacements: dict[str, str],
) -> dict[str, str]:
    """Second-pass call: insert **...** markers WITHOUT changing wording.

    This is to make bolding reliable when the first generation forgets.
    """

    schema = {
        "type": "object",
        "additionalProperties": False,
        "properties": {k: {"type": "string"} for k in replacements.keys()},
        "required": list(replacements.keys()),
    }

    instructions = (
        "You are a formatting pass. You MUST NOT rewrite, paraphrase, reorder, or add/remove words. "
        "Only insert **double-asterisk** markers around existing substrings to indicate bold. "
        "Return ONLY JSON matching the schema."
    )

    # Give concrete constraints to avoid over-bolding.
    user = (
        "For each field value, add bold markers so that:\n"
        "- Summary has 3–6 bold spans\n"
        "- Each Role-Description-* has at least 2 bold spans (one metric/scale, one tool/keyword)\n"
        "- Do not exceed ~20% of characters bolded overall\n"
        "\nHere is the JSON you must minimally modify (bold markers only):\n"
        + json.dumps(replacements, ensure_ascii=False, indent=2)
    )

    resp = client.responses.create(
        model=model,
        instructions=instructions,
        input=user,
        text={
            "format": {
                "type": "json_schema",
                "name": "bold_enforcement",
                "schema": schema,
                "strict": True,
            }
        },
        temperature=0,
    )

    data = json.loads(resp.output_text)
    return {k: str(v) for k, v in data.items()}


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_json_schema(placeholders: list[str]) -> dict[str, Any]:
    # We use placeholders as exact JSON keys so we can directly replace them.
    # JSON property names can contain spaces/symbols, so this is valid.
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




def generate_replacements(
    *,
    api_key: str,
    model: str,
    prompt_rules: str,
    jd_text: str,
    resume_template_text: str,
    placeholders: list[str],
    cache_dir: Path | None = None,
) -> tuple[str, dict[str, str]]:
    """Return (file_name, replacements).

    Replacements are strings and may contain **bold** markers.
    """

    cache_key_payload = json.dumps(
        {
            "cache_version": _CACHE_VERSION,
            "model": model,
            "prompt_rules": prompt_rules,
            "jd": jd_text,
            "resume_template_text": resume_template_text,
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

    schema = _make_json_schema(placeholders)

    # Keep the prompt simple and explicit; structured output enforces shape.
    # We embed the user's rules as-is, but add one extra constraint: output ONLY JSON.
    system = (
        "You generate ATS-friendly resumes by filling placeholders in a Word template. "
        "Return ONLY valid JSON that matches the given JSON schema."
    )

    user = f"""{prompt_rules.strip()}

You must output a JSON object with:
- file_name: MUST be FirstName_LastName_CompanyName_TargetRole.docx (underscores only; NEVER Resume.docx or any generic name)
- replacements: values for EVERY placeholder below

IMPORTANT:
- Use **double-asterisk** markers to indicate bold parts inside replacement values (the renderer will convert them to Word bold runs).
- Do not include the placeholder tokens in the replacement values.
- Do not output any text outside JSON.
- << Main-Title >> = role title ONLY. << Sub-Title-1>>..<< Sub-Title-5 >> = one stack keyword each (not alternate job titles). Template already joins them as: Role | k1 · k2 · k3 · k4 · k5.

<JD>
{jd_text.strip()}
</JD>

<resume>
{resume_template_text.strip()}
</resume>

Placeholders to fill (must include all):
{json.dumps(placeholders, ensure_ascii=False, indent=2)}
"""

    # NOTE: openai-python v2 uses `text={format: ...}` for structured outputs on the Responses API.
    resp = client.responses.create(
        model=model,
        instructions=system,
        input=user,
        text={
            "format": {
                "type": "json_schema",
                "name": "resume_replacements",
                "schema": schema,
                "strict": True,
            }
        },
        temperature=0,
    )

    # responses API returns output text; with json_schema it should be JSON.
    out_text = resp.output_text
    try:
        data = json.loads(out_text)
    except Exception as e:
        raise ResumeGenerationError(f"Model did not return valid JSON: {e}\nRaw: {out_text[:500]}")

    file_name = data.get("file_name")
    replacements = data.get("replacements")
    if not isinstance(file_name, str) or not file_name.lower().endswith(".docx"):
        raise ResumeGenerationError("Invalid file_name in model output")
    if not isinstance(replacements, dict):
        raise ResumeGenerationError("Invalid replacements in model output")

    missing = [ph for ph in placeholders if ph not in replacements]
    if missing:
        raise ResumeGenerationError(f"Missing placeholders in replacements: {missing}")

    # Ensure values are strings
    replacements_str: dict[str, str] = {}
    for k, v in replacements.items():
        if not isinstance(v, str):
            raise ResumeGenerationError(f"Replacement for {k!r} is not a string")
        replacements_str[k] = v

    # NOTE: We intentionally do not do any “company inference/replacement” post-processing.
    # Company names are fixed literal text in the DOCX template.

    # Optional second pass to enforce bold markers if the model forgot.
    if _needs_bold_enforcement(replacements_str):
        replacements_str = enforce_bolding(
            client=client,
            model=model,
            replacements=replacements_str,
        )

    # Normalize role titles to one title per role slot.
    replacements_str = _normalize_role_titles_one_per_slot(replacements_str)

    # Guardrail: prevent apparent seniority regression (e.g., "Software Engineer" -> "Junior").
    replacements_str = _sanitize_title_progression(replacements_str)

    # Clamp header to: Role | exactly up to 5 stack keywords.
    replacements_str = _normalize_main_title(replacements_str)

    # Final guardrail: keep earliest role title non-senior.
    replacements_str = _sanitize_earliest_role_title(replacements_str)

    # Keep the output filename consistent with any title normalization.
    file_name = _normalize_file_name_seniority(file_name, replacements=replacements_str)
    file_name = _ensure_file_name(
        file_name,
        jd_text=jd_text,
        resume_template_text=resume_template_text,
        replacements=replacements_str,
    )

    if cache_dir is not None:
        cache_path.write_text(
            json.dumps({"file_name": file_name, "replacements": replacements_str}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return file_name, replacements_str
