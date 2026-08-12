# Requirements Document

## Introduction

`phase-3-llm-layer` delivers Phase 3 of the MatchLayer roadmap: the LLM Layer. Phases 1 and 2 shipped a deterministic, explainable matching pipeline — semantic similarity plus skill-based coverage behind `POST /api/v1/matches` — with rule-based suggestions. Phase 3 adds the first generative features on top of an existing Match_Result: an AI **resume coach** (overall feedback on the resume against the specific job description), **bullet rewriting** (rewrites of selected resume bullet points targeted at the job), and an **interview question generator** (likely interview questions derived from the resume + job-description pair). All three features anchor to an existing Match_Result and are modeled as sub-resources of a match, so a future standalone coach surface is additive rather than a redesign.

The LLM provider for Phase 3 is **OpenRouter**, accessed with an app-owned API key over OpenRouter's OpenAI-compatible API. The provider sits behind an abstraction layer (per `tech.md`) so a later swap to Amazon Bedrock in Phase 6 is a configuration change, not a code change. The default model is **Claude Haiku 4.5** (`anthropic/claude-haiku-4.5` on OpenRouter), chosen because it is low-cost (~$1/M input tokens) and also available on Amazon Bedrock; the model identifier is configuration, never hardcoded. There is no bring-your-own-key support in this phase.

Responses stream to the UI token-by-token (Server-Sent Events from FastAPI to the Next.js frontend) — the user experience of watching feedback appear live is an explicit priority over implementation simplicity. Streaming coexists with the `conventions.md` structured-outputs rule: streamed tokens are display-progressive, and the persisted result is always a schema-validated structured object; a response that fails final validation takes the fallback path, never a best-effort parse.

Cost control is a first-class requirement. The product cost ceiling for Phases 1–5 is $20/month total (`product.md`), so Phase 3 enforces a per-user daily quota of 25 LLM requests (429 with clear messaging when exceeded) and a global monthly spend circuit breaker (configurable, default $10) that disables LLM features app-wide when crossed. Caching keyed by prompt hash and model — scoped per user, never cross-user — reduces repeat spend.

Security baseline carries forward and gains the `security.md` LLM rules: resume text and job-description text are Restricted PII; prompts are regex-redacted (emails, phone numbers, obvious full names replaced with placeholders) before leaving the system, with a single documented exception — employment history and company names are sent as-is because advice quality requires them; system prompts and user content are strictly delimited against prompt injection; LLM output renders as plain text or safe markdown in React, never `dangerouslySetInnerHTML`; every LLM call is logged with prompt version, model, input hash, output, latency, and cost for Phase 5 evaluation replay — but never with raw Restricted PII; and every LLM failure degrades to a useful non-LLM response, never a 500.

Per ADR 0001 (phase gating), Phase 3 builds nothing for Phase 4+: no LangGraph, no multi-agent workflows, no DeepEval suites (Phase 5 consumes the invocation logs this phase produces). This document does not restate the cross-cutting baselines from `security.md`, `conventions.md`, `structure.md`, or `tech.md`; individual requirements reference the clauses they depend on.

Scope boundaries:

- **In scope:** an LLM provider abstraction (`LLM_Client`) over OpenRouter's OpenAI-compatible API with a configurable model identifier; versioned prompt template files under `apps/api/src/matchlayer_api/ml/prompts/`; PII redaction of prompts before transmission; prompt-injection delimiting; three LLM features (Resume_Coach, Bullet_Rewriter, Interview_Question_Generator) exposed as sub-resources of a Match_Result under `/api/v1/matches/{id}/...`; SSE streaming of responses to the frontend; structured-output validation with non-LLM fallback responses; per-call invocation logging (prompt version, model, input hash, output, latency, cost) persisted for evaluation replay; a per-user daily request quota (default 25, 429 on exceed); a global monthly spend circuit breaker (default $10) that disables LLM features app-wide; per-user LLM response caching keyed by prompt hash and model; `/healthz` reporting LLM subsystem availability following the Phase 2 `semantic_scoring` pattern; results-page UI (tabs/sections) for the three features with streaming rendering and safe output display; OpenAPI→TS/Zod codegen coverage of the new endpoints; configuration, `.env.example`, README runbook, and cost-log updates.
- **Out of scope:** LangGraph or any agent orchestration (Phase 4); DeepEval golden/adversarial evaluation suites and the evaluation dashboard (Phase 5 — Phase 3 only produces the logs those suites replay); bring-your-own-API-key support; a standalone coach page decoupled from a match (the API is shaped so it is additive later); Amazon Bedrock integration (Phase 6 — Phase 3 only guarantees the swap is config-only at the abstraction boundary); fine-tuning or self-hosted LLM inference; applying LLM features to Match_Results owned by other users (recruiter workflows, Phase 7); modifying the Phase 1/2 scoring pipeline, Match_Scorer, or stored Match_Result scoring fields; payment or metered billing (Phase 7).

## Glossary

- **API_App** — The FastAPI application at `apps/api/` exposing the Python package `matchlayer_api`, as established in Phase 1.
- **Web_App** — The Next.js frontend at `apps/web/` established in Phase 1, with the `(app)` authenticated route group that is never indexed per `seo.md`.
- **User_Account** — The authenticated principal from `phase-1-auth` that owns every Resume, Match_Result, and LLM_Result.
- **Match_Result** — A row in the `match_results` table from Phase 1/2, owned by a User_Account, holding the score, breakdown, matched/missing skills, and suggestions for one resume + job-description pair.
- **Resume** — A row in the `resumes` table from Phase 1, owned by a User_Account, holding `extracted_text` (Restricted PII).
- **Job_Description** — The plain-text job description stored with a Match_Result, classified Restricted per `security.md`.
- **LLM_Provider** — The external hosted LLM inference service. In Phase 3 this is OpenRouter, accessed over its OpenAI-compatible API with the app-owned API key.
- **LLM_Client** — The provider abstraction layer in `apps/api/src/matchlayer_api/ml/` through which all LLM calls flow. It exposes a provider-neutral interface (chat-completion with streaming and structured outputs) so that swapping OpenRouter for Amazon Bedrock in Phase 6 changes configuration and at most one provider adapter, and no feature code.
- **LLM_Model** — The model identifier the LLM_Client sends to the LLM_Provider, read from configuration. Default: `anthropic/claude-haiku-4.5`.
- **Prompt_Template** — A versioned prompt file in `apps/api/src/matchlayer_api/ml/prompts/` with a semantic version in the filename (for example `resume_coach.v1.txt`), per `conventions.md`.
- **PII_Redactor** — The Phase 3 component that transforms prompt input text by regex-redacting emails, phone numbers, and obvious full names into placeholders before any text is sent to the LLM_Provider.
- **Redaction_Exception** — The documented policy exception: employment history entries and company names are transmitted unredacted because coaching quality requires them, per the `security.md` requirement that redaction exceptions be documented explicitly.
- **LLM_Feature** — One of the three Phase 3 generative features: Resume_Coach, Bullet_Rewriter, or Interview_Question_Generator.
- **Resume_Coach** — The LLM_Feature that produces a Coaching_Report: overall feedback on a Resume against the Job_Description of a specific Match_Result.
- **Coaching_Report** — The structured output of the Resume_Coach: a schema-validated object containing overall feedback sections (for example strengths, gaps, prioritized improvements).
- **Bullet_Rewriter** — The LLM_Feature that rewrites user-selected resume bullet points to better target the Job_Description of a specific Match_Result.
- **Bullet_Rewrite** — The structured output of the Bullet_Rewriter: for each submitted bullet, the original text paired with one or more rewritten alternatives and a short rationale.
- **Interview_Question_Generator** — The LLM_Feature that produces an Interview_Question_Set from the resume + job-description pair of a specific Match_Result.
- **Interview_Question_Set** — The structured output of the Interview_Question_Generator: a list of likely interview questions, each with a category and the reason it is likely to be asked.
- **LLM_Result** — The persisted, schema-validated structured output of any LLM_Feature invocation (a Coaching_Report, Bullet_Rewrite, or Interview_Question_Set), stored as a sub-resource of the owning Match_Result with a UUIDv7 identifier.
- **LLM_Invocation_Log** — The persisted record of a single LLM_Provider call: prompt version, LLM_Model, input hash, structured output, latency, token usage, and cost. Stored for Phase 5 evaluation replay. Never contains raw Restricted PII.
- **Daily_Quota** — The per-User_Account limit on LLM_Provider-calling requests per UTC day. Default 25, configurable.
- **Spend_Circuit_Breaker** — The global (app-wide, all users) monthly LLM spend limit. Configurable, default $10. When cumulative recorded cost for the calendar month reaches the limit, LLM features are disabled app-wide until the month rolls over or the limit is raised.
- **LLM_Cache** — The cache of completed LLM_Results keyed by prompt hash and LLM_Model, scoped to a single User_Account, never shared across users, per `security.md`.
- **LLM_Unavailable** — The state in which the API_App makes no LLM_Provider calls: the API key was absent at startup, or the Spend_Circuit_Breaker is open. Distinct from a single failed call at runtime, which takes the per-request Fallback_Response path.
- **Fallback_Response** — The degraded but useful non-LLM response an LLM_Feature returns when the LLM call fails or its output fails validation, per the `conventions.md` fallback rule. Each LLM_Feature defines its own Fallback_Response content.
- **SSE_Stream** — A Server-Sent Events HTTP response from the API_App that delivers incremental LLM output tokens/segments to the Web_App, terminated by a completion event carrying the final validated structured result or an error event.
- **Results_Page** — The authenticated Web_App page displaying a Match_Result, extended in Phase 3 with tabs/sections for the three LLM_Features.
- **Rate_Limiter** — The Redis-backed rate limiting infrastructure from `phase-1-auth`, reused for Daily_Quota accounting.

## Requirements

### Requirement 1: LLM Provider Abstraction

**User Story:** As a developer, I want every LLM call to flow through a provider-neutral abstraction, so that moving from OpenRouter to Amazon Bedrock in Phase 6 is a configuration change rather than a rewrite.

#### Acceptance Criteria

1. THE LLM_Client SHALL expose a provider-neutral interface supporting chat-completion requests with streaming output and structured-output enforcement, and every LLM_Provider call made by the API_App SHALL flow through the LLM_Client; provider-neutral means the interface's public method signatures and request/response types SHALL contain no OpenRouter-specific identifiers, types, or parameters, and all OpenRouter-specific code SHALL be confined to a single provider adapter module.
2. THE LLM_Client SHALL communicate with OpenRouter over its OpenAI-compatible API using the app-owned API key read from configuration, and no LLM_Feature, service, or router module SHALL construct an HTTP request to the LLM_Provider directly.
3. THE LLM_Model identifier SHALL be read from the configuration setting `MATCHLAYER_LLM_MODEL` (default `anthropic/claude-haiku-4.5`), and no source file outside configuration defaults SHALL contain a hardcoded model identifier used for LLM calls.
4. THE LLM_Client SHALL read the provider base URL from `MATCHLAYER_LLM_BASE_URL`, the API key from `MATCHLAYER_LLM_API_KEY`, the model identifier from `MATCHLAYER_LLM_MODEL`, the per-request timeout from `MATCHLAYER_LLM_TIMEOUT_SECONDS`, and the per-request maximum output tokens from `MATCHLAYER_LLM_MAX_OUTPUT_TOKENS` (default 4096) via `pydantic-settings` configuration, per the `conventions.md` rule that API keys are accessed through a single config object; and THE LLM_Client SHALL apply the `MATCHLAYER_LLM_MAX_OUTPUT_TOKENS` cap to every request it sends to the LLM_Provider.
5. WHEN the configured LLM_Model value changes between two API_App deployments, THE API_App SHALL use the new model for all subsequent LLM calls without any code change.
6. THE LLM_Client SHALL enforce a configurable per-request wall-clock timeout `MATCHLAYER_LLM_TIMEOUT_SECONDS` (default 60) covering the full call including streaming, and IF the timeout elapses before the call completes, THEN THE LLM_Client SHALL abort the call and signal a failure to the calling LLM_Feature so the Fallback_Response path of Requirement 9 applies.
7. THE API_App SHALL provide no endpoint or configuration path by which a User_Account can supply its own LLM_Provider API key in Phase 3.
8. IF the LLM_Provider API key is absent or empty at API_App startup, THEN THE API_App SHALL start successfully and accept requests, with LLM_Features in the LLM_Unavailable state per Requirement 10 and all non-LLM functionality operating unchanged.
9. THE LLM_Provider API key SHALL be classified Confidential per `security.md`: the key SHALL NOT appear in any log line, error message, RFC 7807 response body, or OpenAPI schema, and SHALL never be sent to the Web_App.
10. WHEN the LLM_Provider API key is present at API_App startup, THE API_App SHALL validate the key against the LLM_Provider during startup, with the validation call bounded by the `MATCHLAYER_LLM_TIMEOUT_SECONDS` timeout of criterion 6, and IF that validation fails, THEN THE API_App SHALL fail startup with an error identifying which cause category applied — key reported invalid by the LLM_Provider, LLM_Provider unreachable, or validation timeout — without exposing the key value.
11. IF the LLM_Provider rejects the key or becomes unreachable at runtime after a successful startup validation, THEN the affected requests SHALL take the per-request Fallback_Response path of Requirement 9 rather than crashing the API_App.
12. WHEN an LLM_Feature request triggers an LLM_Provider call, THE LLM_Client SHALL make at most one call attempt for that request, with no automatic retries in Phase 3, and IF that single attempt fails for any reason, THEN the request SHALL take the Fallback_Response path of Requirement 9 without a further LLM_Provider call.

### Requirement 2: Versioned Prompt Templates

**User Story:** As a developer, I want every prompt to be a versioned file, so that Phase 5 evaluation can replay and compare prompt versions.

#### Acceptance Criteria

1. THE API_App SHALL load every prompt instruction text sent to the LLM_Provider from a UTF-8 Prompt_Template file in `apps/api/src/matchlayer_api/ml/prompts/` whose filename matches the pattern `<feature_name>.v<N>.txt` where `<N>` is a positive integer version (for example `resume_coach.v1.txt`), and no prompt instruction text SHALL be assembled from string literals embedded in service or router code.
2. THE API_App SHALL provide at least one Prompt_Template per LLM_Feature: `resume_coach.v1`, `bullet_rewrite.v1`, and `interview_questions.v1`.
3. WHEN a Prompt_Template's content changes, THE repository SHALL ship the change as a new file with an incremented version number rather than an in-place edit, so that every LLM_Invocation_Log's recorded prompt version identifies immutable prompt content; and a new Prompt_Template version SHALL NOT be introduced with content byte-identical to the immediately preceding version of the same LLM_Feature.
4. THE API_App SHALL resolve the active Prompt_Template version for each LLM_Feature from exactly one designated source (a configuration setting or a single registry module, chosen once and documented), THE resolved version SHALL be the version recorded in the LLM_Invocation_Log for every call that used it, and a prompt rollback SHALL require changing only the active-version value in that one source with no other code change.
5. IF a configured Prompt_Template file is missing or unreadable at the time an LLM_Feature request needs it, THEN THE API_App SHALL treat that request as an LLM failure taking the Fallback_Response path of Requirement 9, and SHALL log a structured event identifying the LLM_Feature, template name, and version, containing no Restricted PII.
6. WHEN an LLM_Feature assembles a prompt for transmission, THE API_App SHALL construct it by substituting runtime values (redacted Resume text, Job_Description text, bullet text, and Match_Result-derived context) only into named placeholders defined in the Prompt_Template, and SHALL add no instruction text at runtime beyond the Prompt_Template content, so the recorded prompt version plus the logged input hash fully determine the transmitted prompt for Phase 5 replay.
7. IF prompt assembly fails because a Prompt_Template placeholder has no corresponding runtime value or the template content cannot be rendered, THEN THE API_App SHALL NOT transmit the partially rendered prompt to the LLM_Provider, SHALL treat the request as an LLM failure taking the Fallback_Response path of Requirement 9, and SHALL log a structured event identifying the template, version, and failing placeholder name, containing no Restricted PII.

### Requirement 3: PII Redaction Before Transmission

**User Story:** As a user, I want my contact details stripped from anything sent to a third-party AI service, so that my personal identifiers stay inside MatchLayer.

#### Acceptance Criteria

1. WHEN an LLM_Feature assembles prompt input from Resume text, Job_Description text, or user-submitted bullet text, THE PII_Redactor SHALL transform that text before the LLM_Client transmits it, replacing detected email addresses, phone numbers, and obvious full names with indexed typed placeholders from the fixed set `[EMAIL_n]`, `[PHONE_n]`, `[NAME_n]`, where each distinct detected value of a type is assigned exactly one index, indices start at 1 per type, and indices are assigned in first-occurrence order within the prompt.
2. THE PII_Redactor SHALL detect email addresses and phone numbers by regex patterns applied to the entire prompt text, and full names by a documented heuristic over the resume's contact/header region; a name detected by the heuristic SHALL be redacted at every occurrence of that name throughout the prompt, not only within the header region, subject to the Redaction_Exception of criterion 3; and the patterns and heuristic SHALL be committed in the repository so redaction behavior is reviewable.
3. THE PII_Redactor SHALL preserve employment history entries and company names exactly as written, without redaction, including when those entries contain values matching the criterion 1 patterns; THE repository documentation SHALL record this Redaction_Exception with its rationale, per the `security.md` requirement that redaction exceptions are documented explicitly; and THE repository documentation SHALL include a committed boundary rule defining what qualifies as an employment history entry or company name for the purposes of this exception, so that classification of any given text span as exempt or redactable is decidable from the documented rule.
4. THE PII_Redactor SHALL be deterministic: identical input text SHALL produce identical redacted output under the same PII_Redactor version; and THE API_App SHALL record the PII_Redactor version in the LLM_Invocation_Log entry for each LLM call, so that Phase 5 evaluation replay can reproduce the exact redacted input.
5. THE PII_Redactor SHALL apply indexed placeholders consistently within one prompt: every occurrence of the same detected value SHALL receive the same indexed placeholder, and distinct detected values of the same type SHALL receive distinct indices, so redacted text remains internally coherent for the LLM.
6. IF the PII_Redactor raises an error, exceeds 5 seconds of wall-clock processing time for a single prompt, or is unavailable, THEN THE API_App SHALL NOT transmit the unredacted text to the LLM_Provider, THE affected request SHALL take the Fallback_Response path of Requirement 9, and THE failure signal (error object, log line, or telemetry event) SHALL NOT contain the input text or any fragment of it.
7. THE PII_Redactor SHALL treat its input as Restricted PII per `security.md`: neither the input text nor the redacted output SHALL appear in any log line, error message, or telemetry signal.
8. THE API_App SHALL compute LLM_Cache keys and LLM_Invocation_Log input hashes only over the redacted text produced by the PII_Redactor, never over the raw pre-redaction text, so that no derived artifact encodes raw PII.
9. THE repository SHALL contain committed synthetic redaction fixtures pairing input texts with their expected redacted outputs, covering repeated occurrences of the same value, multiple distinct values of the same type, and Redaction_Exception cases, containing no real personal data; and THE test suite SHALL verify that the PII_Redactor output matches each fixture's expected output exactly.

### Requirement 4: Prompt Injection Defense

**User Story:** As an operator, I want adversarial text inside resumes and job descriptions to be unable to steer the LLM, so that a crafted document cannot manipulate coaching output or exfiltrate the system prompt.

#### Acceptance Criteria

1. THE API_App SHALL separate system-prompt instructions from user-supplied content (resume text, job-description text, bullet text) using structured message roles, with system-prompt instructions carried only in system-role messages and user-supplied content carried only in user-role messages wrapped in explicit content delimiters, so user-supplied text is never concatenated into the instruction portion of a prompt.
2. Every Prompt_Template SHALL instruct the LLM_Model to treat delimited user content as data to analyze, never as instructions to follow, and SHALL instruct the LLM_Model to never reveal, restate, or summarize the system-prompt instructions in its output.
3. THE LLM_Client SHALL NOT grant the LLM_Model any tool-execution, function-calling side effect, or retrieval capability driven by user-supplied text in Phase 3; the only sanctioned structured mechanism SHALL be schema-constrained output formatting.
4. WHEN user-supplied content contains text resembling instructions (for example "Ignore previous instructions"), THE API_App SHALL still submit it only within the delimited user-content region, unmodified except for the PII_Redactor transforms of Requirement 3 and the delimiter neutralization of criterion 6, relying on the delimiting of criterion 1 rather than content-based filtering that could silently alter resume text.
5. THE structured-output validation of Requirement 8 SHALL apply to every response regardless of input content, so injected instructions that produce off-schema output result in the Fallback_Response path rather than delivery of unvalidated content.
6. IF user-supplied content contains a character sequence matching the content delimiters of criterion 1, THEN THE API_App SHALL deterministically neutralize that sequence (by escaping or substitution) during prompt assembly, so user-supplied text cannot close the user-content region or open an instruction region.

### Requirement 5: Resume Coach

**User Story:** As a job seeker, I want overall AI feedback on my resume against a specific job description, so that I know what to improve beyond the numeric score.

#### Acceptance Criteria

1. WHEN a User_Account requests coaching for a Match_Result it owns and no persisted Coaching_Report exists for that Match_Result under the active Prompt_Template version and configured LLM_Model, THE Resume_Coach SHALL produce a Coaching_Report derived from that Match_Result's Resume text and Job_Description text via the LLM_Client.
2. THE Coaching_Report SHALL be a schema-validated structured object containing at minimum: an overall summary, a list of strengths relative to the Job_Description, a list of gaps or weaknesses, and a list of at least 3 and at most 10 concrete improvement actions, where each action carries an explicit priority rank and the list is ordered from highest to lowest priority; and a response violating these bounds or ordering SHALL fail schema validation per Requirement 8.
3. THE Resume_Coach prompt SHALL include the Match_Result's stored matched skills and missing skills fields, read verbatim from the persisted Match_Result without re-computation, placed within the delimited user-content region per Requirement 4, so coaching is grounded in the deterministic Phase 2 analysis rather than the LLM re-deriving the gap from scratch.
4. WHEN a Coaching_Report is produced and validated, THE API_App SHALL persist it as an LLM_Result associated with the Match_Result and the owning User_Account; and WHEN the owning User_Account subsequently requests or reads coaching for that Match_Result under the same active Prompt_Template version and LLM_Model, THE API_App SHALL return the persisted Coaching_Report without a new LLM_Provider call and without consuming Daily_Quota.
5. IF the LLM call fails or the output fails schema validation, THEN THE Resume_Coach SHALL return a Fallback_Response built from the Match_Result's stored rule-based suggestions and missing skills — including when either stored list is empty, in which case the Fallback_Response SHALL carry the corresponding empty list — marked as degraded per Requirement 9.
6. IF a User_Account requests coaching for a Match_Result it does not own or that does not exist, THEN THE API_App SHALL reject the request with a 404 RFC 7807 response, without invoking the LLM_Client and without revealing whether the Match_Result exists.
7. WHEN a persisted Coaching_Report exists but the active Prompt_Template version or configured LLM_Model has changed since it was produced, THE Resume_Coach SHALL treat a new coaching request as criterion 1 (a new LLM call, subject to Daily_Quota), and THE API_App SHALL retain the previously persisted LLM_Result rather than deleting it.

### Requirement 6: Bullet Rewriting

**User Story:** As a job seeker, I want selected resume bullet points rewritten to better target the job, so that I can strengthen the weakest lines of my resume with concrete alternatives.

#### Acceptance Criteria

1. WHEN a User_Account submits between 1 and `MATCHLAYER_LLM_MAX_BULLETS` (default 5) bullet texts for a Match_Result it owns, THE Bullet_Rewriter SHALL produce a Bullet_Rewrite for the submitted bullets via the LLM_Client.
2. THE Bullet_Rewrite SHALL be a schema-validated structured object containing exactly one entry per submitted bullet, in submission order, where each entry pairs the submitted original text with at least one and at most three rewritten alternatives and a non-empty rationale explaining how the rewrite better targets the Job_Description.
3. THE API_App SHALL validate each submitted bullet text with Pydantic, and IF the submitted bullet count is outside the bounds of criterion 1, any bullet text is empty or whitespace-only, or any bullet text exceeds `MATCHLAYER_LLM_MAX_BULLET_CHARS` (default 500) characters, THEN THE API_App SHALL reject the request with a 422 RFC 7807 response before any LLM_Provider call is made.
4. THE Bullet_Rewriter prompt SHALL include the Job_Description context and the Match_Result's missing skills, so rewrites target the specific job rather than generic resume advice.
5. WHEN a Bullet_Rewrite is produced and validated, THE API_App SHALL persist it as an LLM_Result associated with the Match_Result and the owning User_Account, retrievable on subsequent reads without a new LLM call.
6. IF the LLM call fails or the output fails schema validation, THEN THE Bullet_Rewriter SHALL return a Fallback_Response containing each submitted bullet unchanged, paired with guidance built exclusively from the Match_Result's stored missing skills and rule-based suggestions, marked as degraded per Requirement 9.
7. IF a produced Bullet_Rewrite omits an entry for any submitted bullet or contains an entry whose original text does not exactly match a submitted bullet text, THEN THE API_App SHALL treat the response as a schema-validation failure and take the Fallback_Response path of criterion 6.

### Requirement 7: Interview Question Generation

**User Story:** As a job seeker, I want likely interview questions derived from my resume and the job description, so that I can prepare for the interview this specific application could lead to.

#### Acceptance Criteria

1. WHEN a User_Account requests interview questions for a Match_Result it owns, THE Interview_Question_Generator SHALL produce an Interview_Question_Set derived from that Match_Result's Resume text and Job_Description text via the LLM_Client.
2. THE Interview_Question_Set SHALL be a schema-validated structured object containing a list of questions, where each question carries: the question text (non-empty, at most 300 characters), a category that is exactly one of `technical`, `behavioral`, or `experience-gap`, and a reason (non-empty, at most 500 characters) grounding why this resume + Job_Description pair makes the question likely — with all three fields enforced by the Pydantic schema of Requirement 8.
3. THE Interview_Question_Set produced by the LLM path SHALL contain at least 5 and at most `MATCHLAYER_LLM_MAX_QUESTIONS` (default 15) questions, with both count bounds enforced by the schema validation of Requirement 8.
4. WHEN an Interview_Question_Set is produced and validated, THE API_App SHALL persist it as an LLM_Result associated with the Match_Result and the owning User_Account, retrievable on subsequent reads without a new LLM call.
5. IF the LLM call fails or the output fails schema validation, THEN THE Interview_Question_Generator SHALL return a Fallback_Response of template-based questions derived from the Match_Result's matched and missing skills, marked as degraded per Requirement 9; the Fallback_Response SHALL conform to the Interview_Question_Set structure of criterion 2, SHALL contain at least 5 questions — using generic template questions when the Match_Result's matched and missing skill lists are both empty — and SHALL be produced without any LLM_Provider call per Requirement 9.3.
6. THE Interview_Question_Generator prompt SHALL include the Match_Result's matched and missing skills as context, so that questions — particularly `experience-gap` questions — are grounded in the deterministic Phase 2 analysis rather than the LLM re-deriving the gap from scratch.
7. IF a complete LLM response contains fewer than 5 or more than `MATCHLAYER_LLM_MAX_QUESTIONS` questions, THEN THE API_App SHALL treat the response as a schema-validation failure taking the Fallback_Response path of Requirement 9, and SHALL NOT truncate, pad, or partially deliver the out-of-bounds question list.
8. IF `MATCHLAYER_LLM_MAX_QUESTIONS` is configured with a value below 5, THEN THE API_App SHALL fail startup with an error identifying the misconfigured setting, consistent with the configuration validation of Requirement 18.2.

### Requirement 8: Structured Outputs

**User Story:** As a developer, I want every LLM response validated against a schema, so that production code never parses free-form model text.

#### Acceptance Criteria

1. THE LLM_Client SHALL request schema-constrained output from the LLM_Provider (JSON mode or equivalent structured-output mechanism) for every LLM_Feature call, including streaming calls, regardless of the configured LLM_Model.
2. WHEN a complete LLM response is received, THE API_App SHALL parse it as JSON and validate the parsed value against the Pydantic schema of the requesting LLM_Feature — including that schema's field-level constraints (required fields, types, and bounds such as Requirement 7.3's question count) — before persisting or returning it as an LLM_Result.
3. IF a complete LLM response cannot be parsed as JSON, is truncated, or fails Pydantic schema validation, THEN THE API_App SHALL NOT attempt best-effort parsing, repair, partial extraction, or an automatic re-prompt of the LLM_Provider within the same request, SHALL NOT persist or deliver the invalid response content as an LLM_Result (recording only the failure category per Requirement 12), and the request SHALL take the Fallback_Response path of Requirement 9.
4. THE Pydantic schemas for Coaching_Report, Bullet_Rewrite, Interview_Question_Set, and each LLM_Feature's Fallback_Response envelope (including the Requirement 9.2 fallback marker and failure-reason fields) SHALL be the source of truth exposed through the OpenAPI schema, so the generated TypeScript types and Zod schemas in `packages/shared-types/` cover every LLM_Feature response shape per `conventions.md`, verified by the CI codegen drift check.
5. WHEN an LLM response is delivered as an SSE_Stream per Requirement 11, THE API_App SHALL assemble the complete response from the accumulated streamed content at stream termination and apply the validation of criteria 2 and 3 to that assembled response before emitting the terminal event, so streaming delivery never bypasses schema validation.

### Requirement 9: Fallback Behavior

**User Story:** As a user, I want a useful response even when the AI call fails, so that an LLM outage never turns into a broken page.

#### Acceptance Criteria

1. IF an LLM_Feature's LLM call fails for any reason — provider error, timeout, invalid API key at runtime, schema-validation failure, redaction failure, or missing Prompt_Template — THEN THE API_App SHALL return that feature's Fallback_Response: for a non-streaming request, with HTTP status 200; for a streaming request, via the degraded terminal event of Requirement 11.2 on the already-open SSE_Stream; and THE API_App SHALL NOT return a 5xx status caused solely by the LLM failure. Pre-call rejections — the 429 Daily_Quota response of Requirement 13.2 and the 503 Spend_Circuit_Breaker response of Requirement 10.3 — are not LLM call failures and SHALL retain their own status codes rather than a Fallback_Response.
2. Every Fallback_Response SHALL carry a machine-readable field identifying the response as a fallback (distinguishing it from LLM-produced content), plus a failure-reason field whose value comes from a closed enumerated set covering at minimum: provider error, timeout, schema-validation failure, redaction failure, and missing Prompt_Template; both fields SHALL be defined in the Pydantic response schemas exposed through the OpenAPI schema, so the generated TypeScript types and Zod schemas in `packages/shared-types/` cover them per `conventions.md` and the Web_App can label the content honestly per Requirement 17.4.
3. Every Fallback_Response SHALL be produced exclusively from data already inside MatchLayer that is owned by the requesting User_Account (the target Match_Result's stored fields, matched/missing skills, and rule-based suggestion logic) with no LLM_Provider call, and serving a Fallback_Response SHALL NOT count against the requesting User_Account's Daily_Quota, per Requirement 13.3.
4. WHEN an LLM call fails, THE API_App SHALL emit exactly one structured JSON log event for that failure recording the failure-reason category from the criterion 2 enumerated set, the `request_id`, the requesting User_Account id (id only, never email), the LLM_Feature, and the active Prompt_Template version; and that event SHALL contain no Restricted PII, no prompt content, and no LLM_Provider API key.
5. THE API_App SHALL NOT persist a Fallback_Response as an LLM_Result and SHALL NOT store a Fallback_Response in the LLM_Cache (consistent with Requirement 15.4), so a later retry can produce, persist, and cache the real LLM output.
6. WHEN an LLM_Provider call fails or the `MATCHLAYER_LLM_TIMEOUT_SECONDS` bound of Requirement 1.6 elapses, THE API_App SHALL NOT automatically re-invoke the LLM_Provider for that request before returning the Fallback_Response; each LLM_Feature request SHALL make at most one LLM_Provider call attempt, bounded by the Requirement 1.6 timeout.

### Requirement 10: LLM Availability and Health Reporting

**User Story:** As an operator, I want the system to report whether LLM features are available, so that outages and circuit-breaker trips are observable at a glance.

#### Acceptance Criteria

1. THE `/healthz` endpoint SHALL report an `llm` subsystem status whose value is exactly one of the two literal strings `available` or `unavailable`, following the Phase 2 `semantic_scoring` reporting pattern: the `llm` field is an additive field on the existing `/healthz` response body, exposed in the OpenAPI schema as a two-value enum, and its value SHALL NOT change the endpoint's HTTP status code, which remains 200 whenever the existing `/healthz` checks pass, so an LLM_Unavailable instance is not restart-looped by orchestration.
2. WHILE the API_App is in the LLM_Unavailable state (API key absent at startup, or Spend_Circuit_Breaker open), THE `/healthz` endpoint SHALL report `llm: unavailable`, and THE API_App SHALL respond to LLM_Feature requests per criterion 3 without attempting LLM_Provider calls.
3. WHILE the LLM_Unavailable state is caused by the Spend_Circuit_Breaker, THE API_App SHALL respond to LLM_Feature requests with a 503 RFC 7807 response whose `type` identifies the spend limit as the cause and whose `detail` is safe to display to users and contains no spend figures, API key material, or provider account details; WHILE the state is caused by an absent API key, THE API_App SHALL respond with each feature's Fallback_Response per Requirement 9; and IF both causes hold simultaneously, THEN THE API_App SHALL apply the absent-API-key behavior (Fallback_Response), since no LLM_Provider call is possible regardless of spend state.
4. WHEN the condition causing LLM_Unavailable clears — a valid API key is present at the next API_App startup, the UTC calendar month rolls over, or the configured Spend_Circuit_Breaker limit is raised above the current month's recorded spend — THE API_App SHALL serve the next LLM_Feature request via the LLM_Provider without requiring a code change, and the next `/healthz` response SHALL report `llm: available`.
5. THE `/healthz` `llm` status SHALL NOT expose the API key, spend figures, or provider account details.
6. WHILE the API_App is not in the LLM_Unavailable state, THE `/healthz` endpoint SHALL report `llm: available`.

### Requirement 11: Streaming Delivery

**User Story:** As a user, I want AI feedback to appear token-by-token as it is generated, so that I see progress immediately instead of staring at a spinner.

#### Acceptance Criteria

1. WHEN an LLM_Feature request indicates acceptance of a streaming response via the single streaming-negotiation mechanism documented in the API contract and exposed through the OpenAPI schema, THE API_App SHALL deliver the LLM response as an SSE_Stream that emits incremental content events as tokens/segments arrive from the LLM_Provider; and WHEN an LLM_Feature request does not indicate acceptance of a streaming response, THE API_App SHALL return the complete schema-validated LLM_Result, or the Fallback_Response per Requirement 9, as a single non-streaming response.
2. THE SSE_Stream SHALL terminate with exactly one terminal event, which SHALL be one of: a completion event carrying the final schema-validated structured LLM_Result, or an error/degraded event carrying the Fallback_Response or the RFC 7807 error, so a client never has to assemble the authoritative result from raw streamed tokens; every event SHALL carry a machine-readable event type distinguishing incremental content events from each terminal event kind; and the LLM_Result carried by a completion event SHALL be identical in content to the LLM_Result persisted per Requirements 5, 6, and 7.
3. IF the LLM call fails or validation fails after the SSE_Stream has opened, THEN THE API_App SHALL emit the error/degraded terminal event on the open SSE_Stream — including when the failure occurs before any incremental content event has been emitted — rather than closing the connection without a terminal event; and WHEN a terminal event has been emitted, THE API_App SHALL emit no further events on that SSE_Stream and SHALL close it.
4. THE SSE_Stream endpoints SHALL enforce the same authentication, ownership scoping, Daily_Quota, and Spend_Circuit_Breaker checks as non-streaming requests, evaluated before the stream opens; and IF any of these checks fails, THEN THE API_App SHALL return the same non-streaming RFC 7807 error response as the equivalent non-streaming request (for example the 429 of Requirement 13.2 or the 503 of Requirement 10.3) without opening an SSE_Stream.
5. WHILE an SSE_Stream is open, THE API_App SHALL enforce the `MATCHLAYER_LLM_TIMEOUT_SECONDS` bound of Requirement 1.6 on the underlying LLM call, and on expiry SHALL emit the degraded terminal event per criterion 3.
6. THE incremental content events SHALL contain only content destined for user display, never system-prompt text, provider metadata, or the API key.
7. IF the client disconnects from an open SSE_Stream before a terminal event has been emitted, THEN THE API_App SHALL abort the underlying LLM_Provider call rather than allowing it to run to completion, so a disconnected client does not continue consuming LLM_Provider tokens, and THE API_App SHALL continue serving other requests unaffected.

### Requirement 12: LLM Invocation Logging

**User Story:** As a developer, I want every LLM call recorded with its prompt version, model, input hash, output, latency, and cost, so that Phase 5 evaluation can replay and measure prompt changes.

#### Acceptance Criteria

1. WHEN the LLM_Client completes an LLM_Provider call (streaming or non-streaming, whether it succeeds or fails), THE API_App SHALL persist exactly one LLM_Invocation_Log record for that call, recording: the LLM_Feature, Prompt_Template version, LLM_Model identifier, the PII_Redactor version applied to the prompt input (per Requirement 3), a deterministic hash of the redacted prompt input, the validated structured output (or, when no valid output exists, a failure category drawn from the enumerated failure-category set defined in Requirement 9.2), wall-clock latency measured from the start of request transmission to the LLM_Provider until the final token is received or the call terminates (including termination by timeout or abort), token usage, computed cost, the owning User_Account id, the Match_Result id, and a UTC timestamp.
2. THE LLM_Invocation_Log SHALL record cost from the LLM_Provider's reported usage/cost data when available, otherwise computed from token counts and configured per-token pricing, and the cost basis used SHALL be identifiable from the record; IF an LLM_Provider call fails such that token usage or cost data is unavailable, THEN THE LLM_Invocation_Log SHALL record token usage and cost as explicitly unavailable, in a representation distinguishable from a recorded value of zero.
3. THE LLM_Invocation_Log SHALL NOT contain raw Resume text, raw Job_Description text, unredacted bullet text, or any unredacted Restricted PII; prompt input SHALL be represented by its hash, per the `security.md` rule to reference Restricted data by ID only.
4. THE persisted LLM_Invocation_Log records SHALL be retained and queryable by LLM_Feature, Prompt_Template version, LLM_Model, and time range, so Phase 5 evaluation replay can select comparable invocation sets, and THE API_App SHALL NOT automatically delete, expire, or overwrite LLM_Invocation_Log records in Phase 3.
5. WHEN an individual LLM_Invocation_Log write fails while the underlying storage otherwise remains available, THE API_App SHALL still complete the user-facing request, SHALL NOT return a 5xx response due to that write failure, and SHALL emit a structured log event containing no Restricted PII that records the occurrence of the invocation-log write failure; this guarantee does not extend to complete unavailability of the underlying storage infrastructure, which MAY fail the request through the API_App's normal storage error handling.
6. THE deterministic hash of the redacted prompt input recorded in the LLM_Invocation_Log SHALL be computed using the same hash function and the same redacted prompt input as the LLM_Cache key defined in Requirement 15.1, so that Phase 5 evaluation can correlate LLM_Invocation_Log records with LLM_Cache entries.

### Requirement 13: Per-User Daily Quota

**User Story:** As an operator, I want a hard per-user daily cap on LLM requests, so that one abusive or runaway user cannot burn the budget.

#### Acceptance Criteria

1. THE API_App SHALL enforce a Daily_Quota of `MATCHLAYER_LLM_DAILY_QUOTA` (a positive integer, default 25) LLM_Provider-calling requests per User_Account per UTC day (00:00:00 UTC through 23:59:59 UTC), accounted via the Rate_Limiter.
2. IF the requesting User_Account's counted requests for the current UTC day have reached the Daily_Quota, THEN THE API_App SHALL reject the request with a 429 RFC 7807 response whose `detail` states the configured daily limit and that the quota resets at the next 00:00:00 UTC; and the Daily_Quota check SHALL be the first enforcement step after authentication and ownership verification — before cache lookup, prompt assembly, PII redaction, or any LLM_Provider call.
3. THE API_App SHALL count a request against the Daily_Quota at the moment it initiates an LLM_Provider call for that request; a request served entirely from the LLM_Cache, a request served by a Fallback_Response without an LLM_Provider call, and a request rejected with the 429 response of criterion 2 SHALL NOT be counted, and a counted request SHALL remain counted even if the initiated LLM_Provider call subsequently fails.
4. THE Daily_Quota counter SHALL be scoped per User_Account: one user's consumption SHALL have no effect on any other user's remaining quota.
5. THE API_App SHALL include the requesting User_Account's remaining Daily_Quota count in every LLM_Feature endpoint response, including the 429 rejection of criterion 2, in a single consistent location (response header or response field, identical across all LLM_Feature endpoints) documented in the OpenAPI schema, so the Web_App can display remaining usage.
6. WHEN a User_Account makes an LLM_Feature request at or after 00:00:00 UTC of a new UTC day, THE API_App SHALL evaluate the Daily_Quota check against only that new UTC day's counted requests, so every User_Account's available quota returns to the full configured limit at the UTC day boundary.
7. WHEN multiple LLM_Feature requests from the same User_Account are processed concurrently, THE API_App SHALL perform the Daily_Quota check and count atomically, so that the number of counted LLM_Provider-calling requests for one User_Account in one UTC day never exceeds the Daily_Quota.
8. IF the Rate_Limiter cannot be read or updated during a Daily_Quota check or count, THEN THE API_App SHALL NOT initiate an LLM_Provider call for the affected request and SHALL serve it via the Fallback_Response path of Requirement 9, so an accounting failure can never cause unbounded spend.

### Requirement 14: Global Spend Circuit Breaker

**User Story:** As the product owner, I want an app-wide monthly spend cap on LLM usage, so that a bug or abuse can never push the project past its budget ceiling.

#### Acceptance Criteria

1. THE API_App SHALL compute the Spend_Circuit_Breaker's tracked spend as the sum of the cost recorded in all persisted LLM_Invocation_Log records whose timestamps fall within the current calendar month (UTC), including records for failed LLM_Provider calls that incurred cost.
2. WHEN a Spend_Circuit_Breaker evaluation finds the tracked monthly spend reaches or exceeds `MATCHLAYER_LLM_MONTHLY_SPEND_LIMIT_USD` (default 10), THE Spend_Circuit_Breaker SHALL open, placing LLM_Features in the LLM_Unavailable state app-wide per Requirement 10; and THE API_App SHALL perform a Spend_Circuit_Breaker evaluation before initiating each LLM_Provider call and after persisting each LLM_Invocation_Log record.
3. WHILE the Spend_Circuit_Breaker is open, THE API_App SHALL initiate no new LLM_Provider calls for any User_Account, including SSE_Stream requests per Requirement 11.4; LLM_Provider calls already in flight at the moment the breaker opens MAY run to completion and their cost SHALL be recorded in the LLM_Invocation_Log, so overshoot past the configured limit is bounded by the cost of the calls in flight at that moment.
4. WHEN a Spend_Circuit_Breaker evaluation occurs after the calendar month has rolled over (UTC), THE Spend_Circuit_Breaker SHALL recompute the tracked spend over the new calendar month and SHALL close if the recomputed tracked spend is below the configured limit, without requiring an API_App restart or code change.
5. WHEN the configured `MATCHLAYER_LLM_MONTHLY_SPEND_LIMIT_USD` value in effect is raised above the tracked spend, THE Spend_Circuit_Breaker SHALL close at the first evaluation after the raised limit takes effect, without requiring a code change per Requirement 10.4.
6. WHEN the Spend_Circuit_Breaker opens or closes, THE API_App SHALL log exactly one structured event per transition recording: the transition direction, the tracked spend at the time of transition, the configured limit, and the trigger cause (limit reached, month rollover, limit raised, or tracking failure), containing no Restricted PII and no API key.
7. IF the tracked-spend value cannot be read (for example a storage error), THEN THE Spend_Circuit_Breaker SHALL treat the state as open, THE API_App SHALL serve LLM_Feature requests per Requirement 10.3, and THE API_App SHALL log a structured event recording the read-failure category containing no Restricted PII; WHEN a subsequent evaluation reads the tracked spend successfully and finds it below the configured limit, THE Spend_Circuit_Breaker SHALL close, so a tracking failure can never cause unbounded spend and recovery requires no manual intervention.
8. IF the API_App fails to persist the LLM_Invocation_Log record for a completed LLM_Provider call, so that the call's cost cannot enter the tracked spend, THEN THE Spend_Circuit_Breaker SHALL treat the state as open under the same fail-safe behavior as criterion 7 until a subsequent evaluation successfully reads the tracked spend and finds it below the configured limit, and THE API_App SHALL log a structured event recording the persistence-failure category containing no Restricted PII.

### Requirement 15: Per-User LLM Response Caching

**User Story:** As an operator, I want identical repeat requests served from cache, so that refreshing a page or re-opening a tab never costs another LLM call.

#### Acceptance Criteria

1. WHEN an LLM_Feature produces a validated LLM_Result, THE API_App SHALL cache it in the LLM_Cache keyed by the deterministic hash of the redacted prompt input, the Prompt_Template version, the LLM_Model, and the requesting User_Account's id, so that any change to prompt input, Prompt_Template version, or LLM_Model produces a different cache key and can never serve a stale entry.
2. WHEN an LLM_Feature request's full cache key — including the requesting User_Account's id — matches an unexpired LLM_Cache entry, THE API_App SHALL serve the cached LLM_Result without an LLM_Provider call and without recording a new LLM_Invocation_Log for a provider call that did not occur; the cache lookup SHALL always include the requesting User_Account's id so a lookup can never resolve to another user's entry.
3. THE LLM_Cache SHALL never serve an entry to a User_Account other than the one it was created for, per the `security.md` rule against cross-user cache sharing.
4. THE API_App SHALL NOT cache Fallback_Responses or any LLM output that failed schema validation, so degraded output never masks recovered LLM availability.
5. WHEN a cached LLM_Result is served over a streaming request, THE API_App SHALL make no LLM_Provider call, and THE SSE_Stream SHALL terminate with the completion event of Requirement 11.2 carrying the cached structured result, with or without preceding incremental content events.
6. THE LLM_Cache SHALL expire each entry `MATCHLAYER_LLM_CACHE_TTL_SECONDS` (default 86400, i.e. 24 hours) after the entry was written, and an expired entry SHALL be treated as a cache miss.
7. IF the LLM_Cache is unavailable or a cache lookup fails at request time, THEN THE API_App SHALL process the request as a cache miss and proceed with the normal LLM_Provider call path, and the cache failure SHALL NOT by itself cause a 5xx response.
8. IF writing a new entry to the LLM_Cache fails after a validated LLM_Result is produced, THEN THE API_App SHALL still return that LLM_Result to the requester, and the write failure SHALL NOT cause a 5xx response.

### Requirement 16: API Contract and Ownership Scoping

**User Story:** As a frontend developer, I want the LLM features exposed as well-formed sub-resources of a match with generated types, so that the UI builds against the same contract conventions as every other feature.

#### Acceptance Criteria

1. THE API_App SHALL expose the three LLM_Features as plural kebab-case sub-resource paths beneath `/api/v1/matches/{matchId}/` — `coaching-reports` for the Resume_Coach, `bullet-rewrites` for the Bullet_Rewriter, and `interview-question-sets` for the Interview_Question_Generator — per `conventions.md` and `structure.md` naming rules, with routers under `apps/api/src/matchlayer_api/api/`.
2. WHEN a request targets an LLM_Feature sub-resource of a Match_Result, THE API_App SHALL verify the authenticated User_Account owns that Match_Result, and IF it does not, THEN THE API_App SHALL respond with the same status and RFC 7807 shape it returns for a nonexistent Match_Result, so cross-tenant probing cannot distinguish "not yours" from "not found".
3. Every persisted LLM_Result SHALL carry a UUIDv7 identifier exposed as a string and a `created_at` timestamp in ISO 8601 UTC with `Z` suffix, and WHEN the owning User_Account issues a GET on that LLM_Result's sub-resource path after creation, THE API_App SHALL return the persisted LLM_Result with a 200 status.
4. THE API_App SHALL paginate every LLM_Feature sub-resource list endpoint cursor-based with `?limit=&cursor=` per `conventions.md`, where `limit` defaults to 20 when omitted and accepts values 1 to 100, entries are returned newest-first in descending `created_at` order (equivalently descending UUIDv7 order), and the `cursor` value is an opaque string that clients pass back unmodified.
5. Every error response on the new endpoints SHALL use the RFC 7807 shape from `conventions.md`, with `detail` safe to display and free of stack traces, secrets, and Restricted PII.
6. THE new endpoints and schemas SHALL be covered by the OpenAPI→TypeScript/Zod codegen pipeline, and the CI drift check SHALL fail if committed generated types do not match the OpenAPI output.
7. THE new `/api/v1/*` endpoints SHALL set `X-Robots-Tag: noindex, nofollow` per `seo.md`, consistent with the existing API surface.
8. IF a request to an LLM_Feature sub-resource path carries no valid authentication, THEN THE API_App SHALL respond with a 401 RFC 7807 response without evaluating Match_Result existence or ownership.
9. IF an authenticated User_Account issues a GET for an LLM_Result identifier that does not exist beneath a Match_Result it owns, THEN THE API_App SHALL respond with a 404 RFC 7807 response.
10. IF a list request supplies a `limit` outside the bounds of criterion 4 or a `cursor` that is malformed or does not decode to a valid pagination position, THEN THE API_App SHALL reject it with a 422 RFC 7807 response and SHALL NOT return a partial or unscoped result set.

### Requirement 17: Results Page LLM Experience

**User Story:** As a job seeker, I want the coach, bullet rewriting, and interview questions available right on my match results page with live streaming output, so that acting on my score is one click away.

#### Acceptance Criteria

1. THE Results_Page SHALL present the three LLM_Features as tabs or sections anchored to the displayed Match_Result, within the `(app)` authenticated route group that inherits `noindex, nofollow` per `seo.md`.
2. WHEN a user initiates an LLM_Feature and the API_App streams a response, THE Web_App SHALL render incremental content progressively as SSE events arrive, and WHEN the terminal completion event of Requirement 11.2 arrives, THE Web_App SHALL replace the progressive rendering with the final structured LLM_Result it carries, so progressively rendered tokens are never presented as the authoritative result.
3. THE Web_App SHALL render all LLM-produced content as plain text or as markdown through a renderer that strips or ignores embedded raw HTML (rendering only text-level markdown formatting), and SHALL NOT render LLM content via `dangerouslySetInnerHTML` or any path that executes or injects HTML from model output, per `security.md`.
4. WHEN a response is identified as a Fallback_Response by the machine-readable fallback field of Requirement 9.2, THE Web_App SHALL visibly label the content as generated without AI assistance; content not identified as a Fallback_Response SHALL NOT carry that label.
5. WHEN a request is rejected with the 429 Daily_Quota response, THE Web_App SHALL display a clear message stating the daily limit and when it resets, and WHEN a request is rejected with the 503 spend-limit response, THE Web_App SHALL display a clear message that AI features are temporarily disabled.
6. WHILE an LLM_Feature request is in flight before the first content event arrives, THE Web_App SHALL display a loading state matching the expected content shape (skeleton), per `design.md`.
7. THE Bullet_Rewriter UI SHALL let the user select or paste the bullet texts to rewrite, validating the same count and length bounds as Requirement 6.3 client-side via the generated Zod schemas, and IF the selected or pasted bullets violate those bounds, THEN THE Web_App SHALL display an inline validation error identifying the violated bound and SHALL NOT submit the request to the API_App.
8. WHEN a persisted LLM_Result already exists for the viewed Match_Result, THE Web_App SHALL display the most recently created stored result on load without triggering a new LLM request, and SHALL display only LLM_Results created from the currently viewed Match_Result.
9. THE API_App SHALL include the Prompt_Template version and `created_at` on every returned LLM_Result, and THE Web_App SHALL provide a regenerate action on every displayed LLM_Result that initiates a new LLM_Feature request through the same flow as criterion 2 (subject to the Daily_Quota and Spend_Circuit_Breaker checks), and WHEN the regenerated request completes with a persisted LLM_Result, THE Web_App SHALL display that newest result in place of the previous one.
10. IF the SSE_Stream terminates with the error/degraded terminal event of Requirement 11.2, THEN THE Web_App SHALL discard any partially rendered progressive content and SHALL display the Fallback_Response carried by that event labeled per criterion 4, or, when the event carries an RFC 7807 error, the error's user-safe `detail`.
11. IF the SSE_Stream connection closes without delivering a terminal event, THEN THE Web_App SHALL NOT present the partially rendered content as a final result, and SHALL display an error state indicating the response was interrupted together with an action to retry the LLM_Feature request.

### Requirement 18: Configuration, Documentation, and Cost Tracking

**User Story:** As a developer, I want every new setting documented and the cost story auditable, so that the phase stays reproducible and inside the budget ceiling.

#### Acceptance Criteria

1. THE API_App SHALL define every new Phase 3 configuration setting — `MATCHLAYER_LLM_BASE_URL`, `MATCHLAYER_LLM_API_KEY`, `MATCHLAYER_LLM_MODEL`, `MATCHLAYER_LLM_TIMEOUT_SECONDS`, `MATCHLAYER_LLM_MAX_OUTPUT_TOKENS` (default 4096), `MATCHLAYER_LLM_DAILY_QUOTA`, `MATCHLAYER_LLM_MONTHLY_SPEND_LIMIT_USD`, `MATCHLAYER_LLM_MAX_BULLETS`, `MATCHLAYER_LLM_MAX_BULLET_CHARS` (default 500), `MATCHLAYER_LLM_MAX_QUESTIONS`, and `MATCHLAYER_LLM_CACHE_TTL_SECONDS` (default 86400) — via `pydantic-settings` and list each of them in `.env.example` with a placeholder value, where the `MATCHLAYER_LLM_API_KEY` entry SHALL carry a non-functional placeholder and never a real credential.
2. IF any of `MATCHLAYER_LLM_TIMEOUT_SECONDS`, `MATCHLAYER_LLM_MAX_OUTPUT_TOKENS`, `MATCHLAYER_LLM_DAILY_QUOTA`, `MATCHLAYER_LLM_MONTHLY_SPEND_LIMIT_USD`, `MATCHLAYER_LLM_MAX_BULLETS`, `MATCHLAYER_LLM_MAX_BULLET_CHARS`, `MATCHLAYER_LLM_MAX_QUESTIONS`, or `MATCHLAYER_LLM_CACHE_TTL_SECONDS` is configured with a non-positive value, THEN THE API_App SHALL fail startup with an error message identifying the misconfigured setting by name, so an invalid cost-control or bounds configuration is never silently active.
3. THE repository documentation SHALL include a Phase 3 runbook section covering: obtaining and configuring the OpenRouter key, the default LLM_Model and how to change it via `MATCHLAYER_LLM_MODEL`, the Daily_Quota and Spend_Circuit_Breaker behavior including their default values and reset conditions, and the Redaction_Exception policy with its rationale.
4. THE `docs/costs.md` cost log SHALL be updated with a Phase 3 entry stating the expected monthly LLM cost basis — the configured LLM_Model's per-token pricing, the quota math derived from the `MATCHLAYER_LLM_DAILY_QUOTA` default, and the `MATCHLAYER_LLM_MONTHLY_SPEND_LIMIT_USD` circuit-breaker limit as the worst-case monthly LLM spend — and SHALL show the projected total monthly spend, including pre-existing recurring costs, remains under the $20/month ceiling from `product.md`.
5. THE `.env` file SHALL remain listed in `.gitignore`, and no committed file in the Phase 3 changes SHALL contain the OpenRouter API key value.
6. THE gitleaks pre-commit hook and CI secret scanning from `conventions.md` SHALL apply to all Phase 3 changes unchanged, with no new scanner exclusions, ignore-path entries, or hook bypasses introduced for Phase 3 files.
