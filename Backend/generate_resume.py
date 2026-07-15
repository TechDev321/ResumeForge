from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from resume_generator.openai_resume import ResumeGenerationError
from resume_generator.service import (
    DEFAULT_PROMPT,
    DEFAULT_TEMPLATE,
    generate_resume_docx,
)


def main() -> int:
    load_dotenv()

    ap = argparse.ArgumentParser(description="Generate a resume docx from a template + job description")
    ap.add_argument("--template", default=DEFAULT_TEMPLATE, help="Path to template .docx")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT, help="Path to prompt/rules text")
    ap.add_argument("--jd", default="jd_example.txt", help="Path to job description text file")
    ap.add_argument("--out", default=".", help="Output directory")
    ap.add_argument("--model", default=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"))
    ap.add_argument("--no-cache", action="store_true", help="Disable caching")

    args = ap.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY is not set. Put it in your environment or a .env file.", file=sys.stderr)
        return 2

    jd_path = Path(args.jd)
    if not jd_path.is_file():
        print(f"ERROR: JD file not found: {jd_path}", file=sys.stderr)
        return 2

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    cache_dir = None
    cache_enabled = (os.getenv("RESUME_CACHE", "1") == "1") and (not args.no_cache)
    if cache_enabled:
        cache_dir = Path("cache")

    try:
        file_name, docx_bytes = generate_resume_docx(
            jd_text=jd_path.read_text(encoding="utf-8"),
            api_key=api_key,
            model=args.model,
            template_path=args.template,
            prompt_path=args.prompt,
            cache_dir=cache_dir,
        )
    except (FileNotFoundError, ValueError, ResumeGenerationError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    out_path = out_dir / file_name
    out_path.write_bytes(docx_bytes)
    print(str(out_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
