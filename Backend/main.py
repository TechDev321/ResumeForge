from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field

from resume_generator.openai_cover_letter import CoverLetterGenerationError
from resume_generator.openai_resume import ResumeGenerationError
from resume_generator.service import generate_cover_letter_docx, generate_resume_docx

load_dotenv()

app = FastAPI(title="ResumeForge API", version="1.0.0")

_cors_raw = os.getenv("CORS_ORIGINS", "*").strip()
_cors_origins = [o.strip() for o in _cors_raw.split(",") if o.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    # Required so the browser can read the download name from fetch().
    expose_headers=["Content-Disposition", "X-Filename"],
)


class GenerateRequest(BaseModel):
    jd: str = Field(..., min_length=1, description="Full job description text")


def _docx_attachment(file_name: str, docx_bytes: bytes) -> Response:
    safe_name = re.sub(r"[^\w.\-]+", "_", file_name).strip("._") or "document.docx"
    if not safe_name.lower().endswith(".docx"):
        safe_name = f"{safe_name}.docx"

    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_name}"',
            "X-Filename": safe_name,
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/generate")
def generate(req: GenerateRequest) -> Response:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured on the server.")

    cache_dir = None
    if os.getenv("RESUME_CACHE", "0") == "1":
        cache_dir = Path("cache")

    try:
        file_name, docx_bytes = generate_resume_docx(
            jd_text=req.jd,
            api_key=api_key,
            cache_dir=cache_dir,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except ResumeGenerationError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Generation failed: {e}") from e

    return _docx_attachment(file_name, docx_bytes)


@app.post("/api/generate-cover-letter")
async def generate_cover_letter(
    jd: str = Form(..., min_length=1, description="Full job description text"),
    resume: UploadFile = File(..., description="Generated resume .docx"),
) -> Response:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured on the server.")

    filename = (resume.filename or "").lower()
    if filename and not filename.endswith(".docx"):
        raise HTTPException(status_code=400, detail="Resume must be a .docx file.")

    resume_bytes = await resume.read()
    if not resume_bytes:
        raise HTTPException(status_code=400, detail="Resume file is empty.")

    # Soft cap to avoid oversized uploads (typical tailored resumes are well under this).
    if len(resume_bytes) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Resume file is too large (max 5 MB).")

    cache_dir = None
    if os.getenv("COVER_LETTER_CACHE", "0") == "1":
        cache_dir = Path("cache") / "cover_letter"

    try:
        file_name, docx_bytes = generate_cover_letter_docx(
            jd_text=jd,
            resume_bytes=resume_bytes,
            api_key=api_key,
            cache_dir=cache_dir,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
    except CoverLetterGenerationError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Cover letter generation failed: {e}") from e

    return _docx_attachment(file_name, docx_bytes)
