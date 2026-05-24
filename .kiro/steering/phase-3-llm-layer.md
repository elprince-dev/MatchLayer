---
inclusion: manual
---

# Phase 3 — LLM Layer

**Status:** not started
**Depends on:** Phase 2 shipped.
**Goal:** add LLM-driven coaching — bullet rewriting, keyword optimization, interview question generation. The system should now feel "AI-powered", not just "ML-powered".

## Why this phase exists
Phase 2 tells users *what* matches and what doesn't. Phase 3 tells them *what to do about it* — rewrite this bullet, add this keyword, prepare for these interview questions. This is the leap from "analytics tool" to "career assistant".

## In scope
- **LLM provider abstraction**
  - Single interface (`LLMClient.complete(prompt, model, schema)`) so we can swap providers later.
  - Default: OpenAI `gpt-4o-mini` for cost. Anthropic and local-via-Ollama supported as alternates.
- **Resume Coach**
  - Endpoint: `POST /api/v1/matches/{id}/coach` returning structured suggestions.
  - For each weak bullet: rewrite suggestion + reason.
  - Keyword optimization: list of skills/phrases to add, with where in the resume to add them.
  - Section-level feedback (summary, experience, skills).
- **Interview Question Generator**
  - Endpoint: `POST /api/v1/matches/{id}/interview-questions`.
  - Generates: 5 technical + 5 behavioral questions based on the job description and the candidate's actual background.
  - Each question has: difficulty, category, and a "what they're testing for" note.
- **Prompt engineering discipline (start as we mean to go on)**
  - Every prompt is a versioned text file in `apps/api/src/matchlayer_api/ml/prompts/`. Filename = `<feature>.v<n>.txt`.
  - Use OpenAI structured outputs (JSON mode + Pydantic schema) — never parse free-form text.
  - Every LLM call is logged: prompt version, model, input hash, output, latency, token counts, cost.
  - Fallback path: if the LLM fails or returns invalid JSON twice, return a degraded but useful response (e.g., echo Phase 2 skill gaps with no rewrites). Never 500 to the user.
- **Cost controls**
  - Per-user daily token quota (configurable, default ~50k tokens/day).
  - Cache identical (prompt-hash, model) calls in Redis for 24h.
  - Reject pathologically long inputs early.
- **Frontend**
  - "Coach me" button on the results page → renders rewrite suggestions inline.
  - "Interview prep" tab with the generated questions.
  - Show cost transparency: "This used X tokens / your daily limit".

## Explicitly out of scope
- Multi-step agents (Phase 4).
- Eval framework (Phase 5).
- Streaming responses (nice-to-have, defer unless trivial).
- Fine-tuning.
- RAG over a corpus of resumes / interviews — not needed yet.

## Deliverables
1. Coach endpoint and Interview endpoint live, with versioned prompts in repo.
2. Redis added to docker-compose for caching.
3. LLM call log table (`llm_calls`) populated on every call.
4. Cost dashboard or at least a daily-cost script developers can run.

## Success criteria
- Coach suggestions are useful on 8/10 hand-curated test cases (subjective judgment, document the cases).
- p95 coach latency < 6 seconds (one LLM call, gpt-4o-mini, ~1500 input tokens).
- Zero unstructured-output failures in production after launch (every output passes Pydantic validation).
- Daily LLM spend per active user < $0.10 with quotas in place.

## Skills demonstrated
LLM engineering · prompt versioning · structured outputs · cost control · provider abstraction · degraded-mode design

## Risks & gotchas
- **Hallucinations.** The model will invent skills the user "has". Mitigate by grounding the prompt in the actual resume text and instructing it to cite which bullet it's rewriting. Phase 5's eval suite will catch regressions.
- **Token cost creep.** Every feature wants more context. Set a per-prompt token budget at the abstraction layer and log breaches.
- **Provider lock-in.** The abstraction layer is real protection. Test it by running the full suite against a local Ollama model at least once.
- **PII in prompts.** Resume text contains names, emails, phone numbers. Decide: redact before sending to OpenAI, or accept that OpenAI sees this (most products do, with user consent). Document the choice.
- **Prompt version drift.** Don't edit prompts in place. Always create a new versioned file. Old version stays for replay/rollback.

## Folder additions
```
apps/api/src/matchlayer_api/ml/
  llm/
    __init__.py
    client.py                       # provider abstraction
    openai_client.py
    anthropic_client.py             # optional
    schemas.py                      # Pydantic output schemas
  prompts/
    resume_coach.v1.txt
    interview_questions.v1.txt
apps/api/alembic/versions/
  ...add_llm_calls_table.py
```

DB additions:
- `llm_calls` table: id, user_id, feature, prompt_version, model, input_hash, output_json, latency_ms, prompt_tokens, completion_tokens, cost_usd, status, created_at.
- `users.daily_llm_token_count`, `users.daily_llm_token_count_reset_at` (or move to Redis).

## Work breakdown
1. Add Redis to docker-compose.
2. Create `ml/llm/` module with provider abstraction + OpenAI implementation.
3. Define Pydantic schemas for coach output and interview output.
4. Write `resume_coach.v1.txt` and `interview_questions.v1.txt`.
5. Build coach service: load match → compose prompt → call LLM → validate → persist → return.
6. Build interview service same shape.
7. Add `llm_calls` migration and write-path logging.
8. Implement per-user quota enforcement (Redis counters).
9. Implement (prompt-hash, model) result cache in Redis.
10. Frontend: coach button + suggestion UI; interview tab.
11. Document fallback behavior and test it (kill OpenAI key locally).

## Definition of done
A user can click "Coach me", get structured rewrite suggestions and interview questions in seconds, with every call logged, quota-enforced, and cached. Prompts are versioned in the repo, and the service degrades gracefully when the LLM is unreachable.
