const TOKEN_KEY = "resumeforge_auth_token";

const form = document.getElementById("generate-form");
const jdInput = document.getElementById("jd");
const questionInput = document.getElementById("question");
const button = document.getElementById("generate-btn");
const coverLetterBtn = document.getElementById("cover-letter-btn");
const answerBtn = document.getElementById("answer-btn");
const cancelResumeBtn = document.getElementById("cancel-resume-btn");
const cancelAnswerBtn = document.getElementById("cancel-answer-btn");
const copyAnswerBtn = document.getElementById("copy-answer-btn");
const answerField = document.getElementById("answer-field");
const answerOutput = document.getElementById("answer-output");
const statusEl = document.getElementById("status");
const answerStatusEl = document.getElementById("answer-status");

const authGate = document.getElementById("auth-gate");
const appShell = document.getElementById("app-shell");
const loginForm = document.getElementById("login-form");
const passwordInput = document.getElementById("password-input");
const loginBtn = document.getElementById("login-btn");
const loginStatusEl = document.getElementById("login-status");
const logoutBtn = document.getElementById("logout-btn");

/** @type {boolean} */
let authIsRequired = false;

/** @type {"resume" | "cover" | "answer" | null} */
let activeJob = null;

/** @type {AbortController | null} */
let activeController = null;

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

function getToken() {
  return sessionStorage.getItem(TOKEN_KEY) || "";
}

function setToken(token) {
  if (token) {
    sessionStorage.setItem(TOKEN_KEY, token);
  } else {
    sessionStorage.removeItem(TOKEN_KEY);
  }
}

function authHeaders(extra = {}) {
  const headers = { ...extra };
  const token = getToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
}

function setStatus(message, isError = false) {
  statusEl.textContent = message;
  statusEl.classList.toggle("is-error", isError);
}

function setAnswerStatus(message, isError = false) {
  answerStatusEl.textContent = message;
  answerStatusEl.classList.toggle("is-error", isError);
}

function setLoginStatus(message, isError = false) {
  loginStatusEl.textContent = message;
  loginStatusEl.classList.toggle("is-error", isError);
}

function isAbortError(error) {
  return (
    error &&
    (error.name === "AbortError" ||
      (typeof DOMException !== "undefined" &&
        error instanceof DOMException &&
        error.name === "AbortError"))
  );
}

function startJob(kind) {
  if (activeController) {
    activeController.abort();
  }
  activeJob = kind;
  activeController = new AbortController();
  setBusy(true, kind);
  return activeController;
}

function finishJob() {
  activeJob = null;
  activeController = null;
  setBusy(false);
}

function cancelActiveJob() {
  if (!activeController) return;
  activeController.abort();
}

function showApp() {
  authGate.hidden = true;
  authGate.setAttribute("aria-hidden", "true");
  appShell.hidden = false;
  appShell.removeAttribute("aria-hidden");
  if (logoutBtn) {
    logoutBtn.hidden = !(authIsRequired || getToken());
  }
}

function showGate() {
  cancelActiveJob();
  appShell.hidden = true;
  appShell.setAttribute("aria-hidden", "true");
  authGate.hidden = false;
  authGate.removeAttribute("aria-hidden");
  setToken("");
  lastResume = null;
  if (logoutBtn) {
    logoutBtn.hidden = true;
  }
  setLoginStatus("");
  if (passwordInput) {
    passwordInput.value = "";
    passwordInput.focus();
  }
}

function logout() {
  lastResume = null;
  clearAnswer();
  syncResumeDependentButtons();
  showGate();
  setLoginStatus("Signed out. Enter the password to continue.");
}

function syncResumeDependentButtons() {
  const ready = Boolean(lastResume && lastResume.blob);
  const busy = Boolean(activeJob);
  button.disabled = busy;
  coverLetterBtn.disabled = busy || !ready;
  answerBtn.disabled = busy || !ready;
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

async function apiFetch(url, options = {}) {
  const headers = authHeaders(options.headers || {});
  const response = await fetch(url, { ...options, headers });

  if (response.status === 401) {
    showGate();
    setLoginStatus("Session expired. Please sign in again.", true);
  }

  return response;
}

function setBusy(isBusy, kind = null) {
  if (isBusy) {
    button.disabled = true;
    coverLetterBtn.disabled = true;
    answerBtn.disabled = true;
    cancelResumeBtn.hidden = !(kind === "resume" || kind === "cover");
    cancelAnswerBtn.hidden = kind !== "answer";
  } else {
    cancelResumeBtn.hidden = true;
    cancelAnswerBtn.hidden = true;
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

async function initAuth() {
  if (!API_BASE) {
    showGate();
    setLoginStatus("API base URL is not configured.", true);
    return;
  }

  try {
    const statusRes = await fetch(`${API_BASE}/api/auth/status`);
    if (!statusRes.ok) {
      throw new Error("Could not reach auth status endpoint.");
    }
    const status = await statusRes.json();
    authIsRequired = Boolean(status.auth_required);

    // If the user already unlocked while this request was in flight, keep the app open.
    if (getToken()) {
      showApp();
      return;
    }

    if (!authIsRequired) {
      showApp();
      return;
    }

    showGate();
  } catch (error) {
    if (getToken()) {
      showApp();
      return;
    }
    showGate();
    const message = error instanceof Error ? error.message : "Could not check auth status.";
    setLoginStatus(message, true);
  }
}

loginForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  event.stopPropagation();

  const password = passwordInput.value;
  if (!password) {
    setLoginStatus("Enter the password.", true);
    passwordInput.focus();
    return;
  }

  loginBtn.disabled = true;
  setLoginStatus("Checking password…");

  try {
    const response = await fetch(`${API_BASE}/api/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });

    if (!response.ok) {
      throw new Error(await readErrorDetail(response, "Incorrect password."));
    }

    const data = await response.json();
    authIsRequired = Boolean(data.auth_required);
    if (data.auth_required) {
      if (!data.token) {
        throw new Error("Login succeeded but no session token was returned.");
      }
      setToken(data.token);
    } else {
      setToken("");
    }

    setLoginStatus("");
    showApp();
  } catch (error) {
    setToken("");
    showGate();
    const message = error instanceof Error ? error.message : "Login failed.";
    setLoginStatus(message, true);
  } finally {
    loginBtn.disabled = false;
  }
});

if (logoutBtn) {
  logoutBtn.addEventListener("click", () => {
    logout();
  });
}

cancelResumeBtn.addEventListener("click", () => {
  cancelActiveJob();
});

cancelAnswerBtn.addEventListener("click", () => {
  cancelActiveJob();
});

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

  const controller = startJob("resume");
  clearAnswer();
  setAnswerStatus("");
  setStatus("Generating resume… this can take a minute.");

  try {
    const response = await apiFetch(`${API_BASE}/api/generate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ jd }),
      signal: controller.signal,
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
    if (isAbortError(error)) {
      lastResume = null;
      setStatus("Resume generation cancelled.");
    } else {
      lastResume = null;
      const message = error instanceof Error ? error.message : "Generation failed.";
      setStatus(message, true);
    }
  } finally {
    finishJob();
  }
});

coverLetterBtn.addEventListener("click", async () => {
  const ctx = requireMatchingResume();
  if (!ctx) return;

  const controller = startJob("cover");
  setAnswerStatus("");
  setStatus("Generating cover letter… this can take a minute.");

  try {
    const body = new FormData();
    body.append("jd", ctx.jd);
    body.append("resume", ctx.resume.blob, ctx.resume.filename);

    const response = await apiFetch(`${API_BASE}/api/generate-cover-letter`, {
      method: "POST",
      body,
      signal: controller.signal,
    });

    if (!response.ok) {
      throw new Error(await readErrorDetail(response, `Request failed (${response.status})`));
    }

    const blob = await response.blob();
    const filename = filenameFromHeaders(response, "cover_letter.docx");
    downloadBlob(blob, filename);
    setStatus(`Downloaded ${filename}`);
  } catch (error) {
    if (isAbortError(error)) {
      setStatus("Cover letter generation cancelled.");
    } else {
      const message = error instanceof Error ? error.message : "Cover letter generation failed.";
      setStatus(message, true);
    }
  } finally {
    finishJob();
  }
});

answerBtn.addEventListener("click", async () => {
  const ctx = requireMatchingResume();
  if (!ctx) return;

  const question = questionInput.value.trim();
  if (!question) {
    setAnswerStatus("Enter an interview question first.", true);
    questionInput.focus();
    return;
  }

  const controller = startJob("answer");
  setStatus("");
  setAnswerStatus("Generating answer… this can take a moment.");

  try {
    const body = new FormData();
    body.append("jd", ctx.jd);
    body.append("question", question);
    body.append("resume", ctx.resume.blob, ctx.resume.filename);

    const response = await apiFetch(`${API_BASE}/api/generate-answer`, {
      method: "POST",
      body,
      signal: controller.signal,
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
    setAnswerStatus("Answer ready.");
  } catch (error) {
    if (isAbortError(error)) {
      setAnswerStatus("Answer generation cancelled.");
    } else {
      const message = error instanceof Error ? error.message : "Answer generation failed.";
      setAnswerStatus(message, true);
    }
  } finally {
    finishJob();
  }
});

copyAnswerBtn.addEventListener("click", async () => {
  const text = answerOutput.value;
  if (!text) return;

  try {
    await navigator.clipboard.writeText(text);
    markCopySuccess();
    setAnswerStatus("Answer copied — paste it into your application form.");
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
      setAnswerStatus("Answer copied — paste it into your application form.");
      window.setTimeout(resetCopyButton, 2000);
    } catch {
      setAnswerStatus("Could not copy to clipboard. Select the answer and copy manually.", true);
    }
  }
});

syncResumeDependentButtons();
initAuth();
