---
inclusion: manual
---

# Phase 1 — MVP Foundation

**Status:** not started
**Goal:** build the smallest end-to-end working product. User uploads a resume, pastes a job description, gets an ATS score back.

## Why this phase exists
Get the full vertical slice working — frontend to backend to DB to storage — before adding any intelligence. Infrastructure first, ML/LLM later. A naive scoring algorithm is fine; the point is the plumbing.

## In scope
- **Frontend (Next.js, App Router, TS)**
  - Login / Register pages
  - Resume upload page (PDF/DOCX, max 5MB)
  - Job description input (textarea, paste)
  - Results page showing the ATS score and a basic breakdown
- **Backend (FastAPI)**
  - JWT-based auth (access + refresh tokens)
  - `POST /api/v1/auth/register`, `POST /api/v1/auth/login`, `POST /api/v1/auth/refresh`
  - `POST /api/v1/resumes` (multipart upload → S3, parse text, persist)
  - `POST /api/v1/matches` (resume_id + job_description → score)
  - `GET /api/v1/matches/{id}` (retrieve a stored match)
- **Database (Postgres 16)**
  - `users` (id, email, password_hash, created_at, updated_at, deleted_at)
  - `resumes` (id, user_id, s3_key, original_filename, parsed_text, created_at, deleted_at)
  - `match_results` (id, user_id, resume_id, job_description, score, breakdown_json, created_at)
- **Storage**
  - S3 bucket for raw resume files. In local dev, MinIO via docker-compose.
- **Scoring (the "AI" — keep it simple)**
  - Parse resume text via `pypdf` (PDF) and `python-docx` (DOCX).
  - Tokenize resume + JD, lowercase, strip stopwords.
  - Compute TF-IDF cosine similarity → 0–100 score.
  - Extract a keyword overlap list for the breakdown ("matched skills" / "missing skills") using a hard-coded skills lexicon (~200 common tech skills from a static JSON file).
- **Local dev**
  - `docker-compose.yml` with Postgres + MinIO.
  - `.env.example` checked in.
  - One-command spin-up documented in root README.
- **Deploy target**
  - Frontend: Vercel (free tier).
  - Backend: a single container somewhere reachable — Render, Railway, or Fly.io free tier. ECS deferred to Phase 6.
  - DB: managed Postgres on the same provider, or Supabase free tier.

## Explicitly out of scope
- Embeddings, vector search, pgvector
- Any LLM call (OpenAI, etc.)
- Agents, LangGraph
- Resume versioning, history, comparison
- Stripe, multi-tenancy, admin dashboard
- SQS or any async processing — scoring runs synchronously in the request
- Real-time updates / WebSockets
- Email verification, password reset (skip for MVP, log a TODO)

## Deliverables
1. Deployed app at a public URL — anyone can register, upload a resume, paste a JD, see a score.
2. README with: architecture diagram, local setup, deploy steps, demo credentials.
3. CI: lint + tests on every PR (GitHub Actions, even if minimal).
4. A short "what's next" section in the README pointing at Phase 2.

## Success criteria
- Cold-start: a new user can go from landing page to ATS score in < 2 minutes.
- Score endpoint returns in < 3 seconds for a typical 2-page resume.
- All happy-path flows have at least one integration test.
- No secrets in git history.

## Skills demonstrated
Next.js App Router · FastAPI · async SQLAlchemy · JWT auth · S3 file uploads · TF-IDF scoring · Docker + docker-compose · CI basics · public deployment

## Risks & gotchas
- **Resume parsing accuracy.** PDFs are hostile. `pypdf` handles most but image-only PDFs return empty text. Add a clear error message and reject files with < 100 chars extracted.
- **Auth security.** Store hashed passwords with `argon2-cffi`. Use HttpOnly cookies for refresh tokens, return access tokens in JSON. Set sensible CORS.
- **File upload size.** Enforce 5MB limit on both frontend and FastAPI side. Reject early.
- **TF-IDF is dumb.** It will look impressive only because users haven't seen Phase 2 yet. Be honest in the UI: "Basic keyword match — semantic analysis coming soon."

## Folder additions (relative to repo root)
```
apps/web/                          # Next.js app — initialize fresh
apps/api/                          # FastAPI app — initialize fresh
apps/api/src/matchlayer_api/api/auth/
apps/api/src/matchlayer_api/api/resumes/
apps/api/src/matchlayer_api/api/matches/
apps/api/src/matchlayer_api/services/
apps/api/src/matchlayer_api/db/models/
apps/api/alembic/
infra/docker/                      # Dockerfiles for web + api
docker-compose.yml                 # postgres + minio
.github/workflows/ci.yml
```

## Work breakdown (rough — refine in spec)
1. Repo scaffold: `pnpm-workspace.yaml`, root `package.json`, `apps/web` via `create-next-app`, `apps/api` via `uv init`.
2. Local dev: `docker-compose.yml` with Postgres + MinIO, `.env.example`.
3. Backend: FastAPI skeleton, settings, structlog, request-id middleware, health endpoint.
4. Backend: SQLAlchemy + Alembic, initial migration with users / resumes / match_results.
5. Backend: auth module (register, login, refresh, password hashing, JWT issuance).
6. Backend: resume upload — multipart parsing, S3 client, text extraction service.
7. Backend: match service — TF-IDF scoring, keyword overlap, persist results.
8. Frontend: Next.js skeleton, Tailwind, shadcn/ui setup, layout.
9. Frontend: auth pages + cookie/session handling.
10. Frontend: resume upload page, JD input, results page.
11. CI: GitHub Actions running ruff, mypy, pytest, eslint, vitest.
12. Deploy: pick platforms, write deploy docs, wire up env vars.

## Definition of done
A new visitor can register, upload a resume, paste a JD, and see a score and a matched-skills breakdown — all running on a public URL with HTTPS, with code in `main` and CI green.
