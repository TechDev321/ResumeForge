# ResumeForge

ResumeForge is a small FastAPI + static frontend app that generates tailored resumes and cover letters from a job description.

## Project Structure

```text
Backend/   FastAPI API, OpenAI resume/cover letter generation, DOCX templates
Frontend/  Static HTML/CSS/JS app
```

## Run Backend Locally

From the repo root:

```powershell
cd Backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Create `Backend/.env` from `Backend/.env.example` and set:

```env
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-4.1-mini
CORS_ORIGINS=http://127.0.0.1:5500,http://localhost:5500,http://127.0.0.1:3000,http://localhost:3000
RESUME_CACHE=0
COVER_LETTER_CACHE=0
```

Start the API:

```powershell
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Test:

```text
http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok"}
```

## Run Frontend Locally

The frontend is a static site. From the repo root:

```powershell
cd Frontend
npm run build
```

Then serve the folder with any static server. Example with Python:

```powershell
python -m http.server 5500
```

Open:

```text
http://127.0.0.1:5500
```

For localhost, `Frontend/app.js` automatically uses:

```text
http://127.0.0.1:8000
```

So no frontend env var is required for local development.

## Local Workflow

1. Start backend on `http://127.0.0.1:8000`
2. Start frontend on `http://127.0.0.1:5500`
3. Paste a job description
4. Click **Generate resume**
5. After the resume downloads, click **Generate cover letter**

The cover letter flow keeps the generated resume in browser memory and sends it automatically to the backend. No manual re-upload is needed.

## Deploy Notes

### Render Backend

Backend build command:

```bash
pip install -r requirements.txt
```

Backend start command:

```bash
uvicorn main:app --host 0.0.0.0 --port $PORT
```

Required Render environment variables:

```env
OPENAI_API_KEY=your_openai_api_key
OPENAI_MODEL=gpt-4.1-mini
CORS_ORIGINS=https://your-vercel-app.vercel.app
RESUME_CACHE=0
COVER_LETTER_CACHE=0
```

### Vercel Frontend

Set Vercel root directory to:

```text
Frontend
```

Set Vercel environment variable:

```env
RESUMEFORGE_API_BASE=https://your-render-backend.onrender.com
```

The frontend build writes that value into `config.js`.

Build command:

```bash
npm run build
```

Output directory:

```text
.
```
