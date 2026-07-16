from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from openai import OpenAI


class QuestionGenerationError(RuntimeError):
    pass


_CACHE_VERSION = 1

_DASH_RE = re.compile(r"[—–]")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sanitize_dashes(text: str) -> str:
    return _DASH_RE.sub("-", text or "")


def _load_prompt_rules(prompt_rules: str) -> str:
    """Strip trailing conversational prompts like 'Are you ready?'."""

    lines = [ln.rstrip() for ln in (prompt_rules or "").splitlines()]
    while lines and lines[-1].strip().lower() in {"are you ready?", "are you ready"}:
        lines.pop()
    return "\n".join(lines).strip()


def _make_json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "answer": {
                "type": "string",
                "description": "Brief spoken-style answer, ~100 words, multi-line for readability.",
            }
        },
        "required": ["answer"],
    }


def generate_interview_answer(
    *,
    api_key: str,
    model: str,
    prompt_rules: str,
    jd_text: str,
    resume_text: str,
    question_text: str,
    cache_dir: Path | None = None,
) -> str:
    """Return a plain-text interview answer grounded in JD + resume."""

    jd = (jd_text or "").strip()
    resume = (resume_text or "").strip()
    question = (question_text or "").strip()
    if not jd:
        raise ValueError("Job description is empty")
    if not resume:
        raise ValueError("Resume text is empty")
    if not question:
        raise ValueError("Question is empty")

    rules = _load_prompt_rules(prompt_rules)

    cache_key_payload = json.dumps(
        {
            "cache_version": _CACHE_VERSION,
            "model": model,
            "prompt_rules": rules,
            "jd": jd,
            "resume_text": resume,
            "question": question,
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
            return obj["answer"]

    client = OpenAI(api_key=api_key)
    schema = _make_json_schema()

    system = (
        "You answer job-application interview questions using only the candidate's resume "
        "and the target job description. Return ONLY valid JSON matching the given schema."
    )

    user = f"""{rules}

Answer the interview question below.

IMPORTANT:
- Base claims only on the resume; do not invent employers, dates, or credentials.
- Follow all style rules above (STAR for behavioral questions, ~100 words, spoken tone).
- Put each thought on its own line for readability.
- Plain text only in the answer (no markdown, no bullets).

<JD>
{jd}
</JD>

<resume>
{resume}
</resume>

<question>
{question}
</question>
"""

    resp = client.responses.create(
        model=model,
        instructions=system,
        input=user,
        text={
            "format": {
                "type": "json_schema",
                "name": "interview_answer",
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
        raise QuestionGenerationError(f"Model did not return valid JSON: {e}\nRaw: {out_text[:500]}")

    answer = data.get("answer")
    if not isinstance(answer, str) or not answer.strip():
        raise QuestionGenerationError("Invalid answer in model output")

    answer = _sanitize_dashes(answer.strip())

    if cache_dir is not None:
        cache_path.write_text(
            json.dumps({"answer": answer}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return answer
