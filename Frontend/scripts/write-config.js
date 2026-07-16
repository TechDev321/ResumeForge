const fs = require("fs");
const path = require("path");

const url = String(process.env.RESUMEFORGE_API_BASE || "")
  .trim()
  .replace(/\/$/, "");

const out = path.join(__dirname, "..", "config.js");
fs.writeFileSync(
  out,
  `window.RESUMEFORGE_API_BASE = ${JSON.stringify(url)};\n`,
  "utf8",
);

console.log(
  url
    ? `Wrote config.js with RESUMEFORGE_API_BASE=${url}`
    : "Wrote config.js with empty RESUMEFORGE_API_BASE (localhost fallback)",
);
