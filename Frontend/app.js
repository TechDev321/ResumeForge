const form = document.getElementById("generate-form");
const jdInput = document.getElementById("jd");
const button = document.getElementById("generate-btn");
const statusEl = document.getElementById("status");

// Local: http://127.0.0.1:8000
// Production: set window.RESUMEFORGE_API_BASE before this script, or edit below.
const API_BASE = (
  window.RESUMEFORGE_API_BASE ||
  (location.hostname === "localhost" || location.hostname === "127.0.0.1"
    ? "http://127.0.0.1:8000"
    : "https://YOUR-SERVICE.onrender.com")
).replace(/\/$/, "");

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("is-error", isError);
}

function filenameFromHeaders(response) {
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
  return match ? match[1] : "resume.docx";
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

form.addEventListener("submit", async (event) => {
  event.preventDefault();

  const jd = jdInput.value.trim();
  if (!jd) {
    setStatus("Paste a job description first.", true);
    jdInput.focus();
    return;
  }

  button.disabled = true;
  setStatus("Generating resume… this can take a minute.");

  try {
    const response = await fetch(`${API_BASE}/api/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jd }),
    });

    if (!response.ok) {
      let detail = `Request failed (${response.status})`;
      try {
        const err = await response.json();
        if (err && err.detail) {
          detail = typeof err.detail === "string" ? err.detail : JSON.stringify(err.detail);
        }
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }

    const blob = await response.blob();
    const filename = filenameFromHeaders(response);
    downloadBlob(blob, filename);
    setStatus(`Downloaded ${filename}`);
  } catch (error) {
    const message = error instanceof Error ? error.message : "Generation failed.";
    setStatus(message, true);
  } finally {
    button.disabled = false;
  }
});
