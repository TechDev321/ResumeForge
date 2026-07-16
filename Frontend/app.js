const form = document.getElementById("generate-form");
const jdInput = document.getElementById("jd");
const button = document.getElementById("generate-btn");
const coverLetterBtn = document.getElementById("cover-letter-btn");
const statusEl = document.getElementById("status");

// Kept in memory after resume generate — sent automatically for cover letter.
/** @type {{ blob: Blob, filename: string, jd: string } | null} */
let lastResume = null;

// Local: http://127.0.0.1:8000
// Production: set RESUMEFORGE_API_BASE on Vercel; build writes it into config.js.
const API_BASE = (
  window.RESUMEFORGE_API_BASE ||
  (location.hostname === "localhost" || location.hostname === "127.0.0.1"
    ? "http://127.0.0.1:8000"
    : "")
).replace(/\/$/, "");

if (!API_BASE) {
  console.error(
    "RESUMEFORGE_API_BASE is not set. Configure it on Vercel and redeploy.",
  );
}

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("is-error", isError);
}

function syncCoverLetterButton() {
  const ready = Boolean(lastResume && lastResume.blob);
  coverLetterBtn.disabled = !ready;
  coverLetterBtn.title = ready
    ? "Generate a cover letter from the last resume"
    : "Generate a resume first";
}

function filenameFromHeaders(response, fallback = "resume.docx") {
  const custom = response.headers.get("X-Filename");
  if (custom) return custom;

  const disposition = response.headers.get("Content-Disposition") || "";
  const utfMatch = /filename\*\s*=\s*UTF-8''([^;]+)/i.exec(disposition);
  if (utfMatch) {
    try {
      return decodeURIComponent(utfMatch[1].trim());
    } catch {
      /* fall through */
    }
  }
  const match = /filename="([^"]+)"/i.exec(disposition);
  return match ? match[1] : fallback;
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function readErrorDetail(response, fallback) {
  let detail = fallback;
  try {
    const err = await response.json();
    if (err && err.detail) {
      detail = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail);
    }
  } catch {
    /* ignore */
  }
  return detail;
}

function setBusy(isBusy) {
  button.disabled = isBusy;
  if (isBusy) {
    coverLetterBtn.disabled = true;
  } else {
    syncCoverLetterButton();
  }
}

jdInput.addEventListener("input", () => {
  if (!lastResume) return;
  if (jdInput.value.trim() !== lastResume.jd) {
    lastResume = null;
    syncCoverLetterButton();
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const jd = jdInput.value.trim();
  if (!jd) {
    setStatus("Paste a job description first.", true);
    jdInput.focus();
    return;
  }

  setBusy(true);
  setStatus("Generating resume… this can take a minute.");

  try {
    const response = await fetch(`${API_BASE}/api/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jd }),
    });

    if (!response.ok) {
      throw new Error(await readErrorDetail(response, `Request failed (${response.status})`));
    }

    const blob = await response.blob();
    const filename = filenameFromHeaders(response, "resume.docx");
    lastResume = { blob, filename, jd };
    downloadBlob(blob, filename);
    setStatus(`Downloaded ${filename}. You can generate a cover letter next.`);
  } catch (error) {
    lastResume = null;
    const message = error instanceof Error ? error.message : "Generation failed.";
    setStatus(message, true);
  } finally {
    setBusy(false);
  }
});

coverLetterBtn.addEventListener("click", async () => {
  if (!lastResume) {
    setStatus("Generate a resume first.", true);
    return;
  }

  const jd = jdInput.value.trim();
  if (!jd) {
    setStatus("Paste a job description first.", true);
    jdInput.focus();
    return;
  }

  if (jd !== lastResume.jd) {
    lastResume = null;
    syncCoverLetterButton();
    setStatus("Job description changed — generate a new resume first.", true);
    return;
  }

  setBusy(true);
  setStatus("Generating cover letter… this can take a minute.");

  try {
    const body = new FormData();
    body.append("jd", jd);
    body.append("resume", lastResume.blob, lastResume.filename);

    const response = await fetch(`${API_BASE}/api/generate-cover-letter`, {
      method: "POST",
      body,
    });

    if (!response.ok) {
      throw new Error(await readErrorDetail(response, `Request failed (${response.status})`));
    }

    const blob = await response.blob();
    const filename = filenameFromHeaders(response, "cover_letter.docx");
    downloadBlob(blob, filename);
    setStatus(`Downloaded ${filename}`);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Cover letter generation failed.";
    setStatus(message, true);
  } finally {
    setBusy(false);
  }
});

syncCoverLetterButton();
