const form = document.getElementById("generate-form");
const jdInput = document.getElementById("jd");
const questionInput = document.getElementById("question");
const button = document.getElementById("generate-btn");
const coverLetterBtn = document.getElementById("cover-letter-btn");
const answerBtn = document.getElementById("answer-btn");
const copyAnswerBtn = document.getElementById("copy-answer-btn");
const answerField = document.getElementById("answer-field");
const answerOutput = document.getElementById("answer-output");
const statusEl = document.getElementById("status");

// Kept in memory after resume generate — sent automatically for cover letter / answers.
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

function syncResumeDependentButtons() {
  const ready = Boolean(lastResume && lastResume.blob);
  coverLetterBtn.disabled = !ready;
  answerBtn.disabled = !ready;
  coverLetterBtn.title = ready
    ? "Generate a cover letter from the last resume"
    : "Generate a resume first";
  answerBtn.title = ready
    ? "Generate an interview answer from the last resume"
    : "Generate a resume first";
}

function clearAnswer() {
  answerOutput.value = "";
  answerField.hidden = true;
  copyAnswerBtn.disabled = true;
  resetCopyButton();
}

function showAnswer(text) {
  answerOutput.value = text;
  answerField.hidden = false;
  copyAnswerBtn.disabled = !text;
  resetCopyButton();
}

function resetCopyButton() {
  copyAnswerBtn.classList.remove("is-copied");
  const copyIcon = copyAnswerBtn.querySelector(".icon-copy");
  const checkIcon = copyAnswerBtn.querySelector(".icon-check");
  const label = copyAnswerBtn.querySelector(".copy-label");
  if (copyIcon) copyIcon.hidden = false;
  if (checkIcon) checkIcon.hidden = true;
  if (label) label.textContent = "Copy";
  copyAnswerBtn.title = "Copy answer";
  copyAnswerBtn.setAttribute("aria-label", "Copy answer to clipboard");
}

function markCopySuccess() {
  copyAnswerBtn.classList.add("is-copied");
  const copyIcon = copyAnswerBtn.querySelector(".icon-copy");
  const checkIcon = copyAnswerBtn.querySelector(".icon-check");
  const label = copyAnswerBtn.querySelector(".copy-label");
  if (copyIcon) copyIcon.hidden = true;
  if (checkIcon) checkIcon.hidden = false;
  if (label) label.textContent = "Copied";
  copyAnswerBtn.title = "Copied";
  copyAnswerBtn.setAttribute("aria-label", "Answer copied");
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
    answerBtn.disabled = true;
  } else {
    syncResumeDependentButtons();
  }
}

function requireMatchingResume() {
  if (!lastResume) {
    setStatus("Generate a resume first.", true);
    return null;
  }

  const jd = jdInput.value.trim();
  if (!jd) {
    setStatus("Paste a job description first.", true);
    jdInput.focus();
    return null;
  }

  if (jd !== lastResume.jd) {
    lastResume = null;
    syncResumeDependentButtons();
    clearAnswer();
    setStatus("Job description changed — generate a new resume first.", true);
    return null;
  }

  return { jd, resume: lastResume };
}

jdInput.addEventListener("input", () => {
  if (!lastResume) return;
  if (jdInput.value.trim() !== lastResume.jd) {
    lastResume = null;
    syncResumeDependentButtons();
    clearAnswer();
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
  clearAnswer();
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
    setStatus(`Downloaded ${filename}. You can generate a cover letter or answer next.`);
  } catch (error) {
    lastResume = null;
    const message = error instanceof Error ? error.message : "Generation failed.";
    setStatus(message, true);
  } finally {
    setBusy(false);
  }
});

coverLetterBtn.addEventListener("click", async () => {
  const ctx = requireMatchingResume();
  if (!ctx) return;

  setBusy(true);
  setStatus("Generating cover letter… this can take a minute.");

  try {
    const body = new FormData();
    body.append("jd", ctx.jd);
    body.append("resume", ctx.resume.blob, ctx.resume.filename);

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

answerBtn.addEventListener("click", async () => {
  const ctx = requireMatchingResume();
  if (!ctx) return;

  const question = questionInput.value.trim();
  if (!question) {
    setStatus("Enter an interview question first.", true);
    questionInput.focus();
    return;
  }

  setBusy(true);
  setStatus("Generating answer… this can take a moment.");

  try {
    const body = new FormData();
    body.append("jd", ctx.jd);
    body.append("question", question);
    body.append("resume", ctx.resume.blob, ctx.resume.filename);

    const response = await fetch(`${API_BASE}/api/generate-answer`, {
      method: "POST",
      body,
    });

    if (!response.ok) {
      throw new Error(await readErrorDetail(response, `Request failed (${response.status})`));
    }

    const data = await response.json();
    const answer = data && typeof data.answer === "string" ? data.answer : "";
    if (!answer) {
      throw new Error("No answer returned from the server.");
    }

    showAnswer(answer);
    setStatus("Answer ready.");
  } catch (error) {
    const message = error instanceof Error ? error.message : "Answer generation failed.";
    setStatus(message, true);
  } finally {
    setBusy(false);
  }
});

copyAnswerBtn.addEventListener("click", async () => {
  const text = answerOutput.value;
  if (!text) return;

  try {
    await navigator.clipboard.writeText(text);
    markCopySuccess();
    setStatus("Answer copied — paste it into your application form.");
    window.setTimeout(() => {
      if (copyAnswerBtn.classList.contains("is-copied")) {
        resetCopyButton();
      }
    }, 2000);
  } catch {
    try {
      answerOutput.focus();
      answerOutput.select();
      document.execCommand("copy");
      markCopySuccess();
      setStatus("Answer copied — paste it into your application form.");
      window.setTimeout(resetCopyButton, 2000);
    } catch {
      setStatus("Could not copy to clipboard. Select the answer and copy manually.", true);
    }
  }
});

syncResumeDependentButtons();
