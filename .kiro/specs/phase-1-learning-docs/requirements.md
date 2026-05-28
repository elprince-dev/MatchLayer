# Requirements Document

## Introduction

`phase-1-learning-docs` creates a structured, beginner-friendly learning library that explains, from first principles, every technology, library, design choice, and convention used in MatchLayer Phase 1. The deliverable is a set of long-form Markdown documents written for a self-described junior developer who has not previously used many of the tools in the stack (Docker and Compose, FastAPI async, JWT, Argon2id, pgvector, structured logging, the OpenAPI codegen pipeline, AWS CDK, distroless containers, etc.). The "system" under specification is therefore a folder of documentation files with a defined structure, a standardized template, and explicit content-quality rules — not application code.

This spec covers Phase 1 only. The folder structure and doc template are designed so Phases 2 through 7 can plug additional sub-libraries in later without restructuring.

Decisions made during requirements drafting (open to override during review):

- **Location:** `docs/learning/` at the repo root, with `docs/learning/phase-1/` for this phase. The existing `docs/` directory is already reserved for architecture docs, ADRs, and runbooks (per `.kiro/steering/structure.md`); `docs/learning/` slots alongside them as the long-form companion to the steering docs.
- **Doc template:** each topic gets one Markdown file with a fixed set of required sections (Introduction, Problem it solves, Mental model, How it works, MatchLayer Phase 1 usage, Common pitfalls, External reading). A "Hands-on checkpoint" section is optional per topic, placed immediately before External reading when present.
- **Depth:** thoroughness over brevity. No upper bound on length. Each topic is written so a junior developer with zero prior knowledge can follow it.
- **Maintenance:** when a Phase 1 implementation file referenced in a Topic_Doc changes, the corresponding Topic_Doc is updated in the same pull request.

Scope boundaries:

- **In scope:** the `docs/learning/` library structure and conventions, the `docs/learning/phase-1/` sub-library and its index, the canonical Doc_Template, Topic_Docs covering every Phase 1 topic area named in the Phase_1_Topic_Coverage_List (foundation and tooling, frontend, backend, security, database and storage, containerization, contracts and codegen, hosting and deploy), file-reference and code-snippet accuracy rules, internal and external link integrity rules, and beginner-accessibility content rules.
- **Out of scope:** auto-generated API reference (OpenAPI already covers that), ADRs (those live in `docs/adr/` and capture decisions, not tutorials), per-app READMEs (those live next to each app), the steering docs in `.kiro/steering/` (those are the always-loaded summaries), Phase 2 through Phase 7 content, automated link-checking CI infrastructure (treated as a future enhancement), and any Phase 1 application code or configuration changes.

## Glossary

- **Learning_Docs_Library** — The folder rooted at `docs/learning/` together with its top-level README, conventions document, and per-phase sub-libraries. The umbrella library is multi-phase by design; this spec only populates the Phase 1 sub-library.
- **Library_Index** — The Markdown file at `docs/learning/README.md` that introduces the Learning_Docs_Library, lists the available phases, and points to the Conventions_Doc.
- **Conventions_Doc** — The Markdown file at `docs/learning/CONVENTIONS.md` that specifies the canonical Doc_Template, naming rules, file-reference rules, code-snippet rules, link rules, and the beginner-accessibility content bar.
- **Phase_1_Sub_Library** — The folder `docs/learning/phase-1/` and every Topic_Doc inside it.
- **Phase_1_Index** — The Markdown file at `docs/learning/phase-1/README.md` that introduces Phase 1, lists every Topic_Doc with a one-line summary, and groups Topic_Docs into thematic sections.
- **Topic_Doc** — A single Markdown file inside a phase sub-library that explains one topic in depth.
- **Doc_Template** — The standardized section structure that every Topic_Doc follows, defined in the Conventions_Doc.
- **Phase_1_Topic_Coverage_List** — The canonical list of topics that the Phase_1_Sub_Library must cover, defined in this requirements document.
- **Reader** — The intended reader of every Topic_Doc: a junior developer with no prior exposure to the topic. Used as the calibration anchor for beginner-accessibility rules.
- **Phase_1_Foundation_Spec** — The existing spec at `.kiro/specs/phase-1-foundation/` that describes the implementation under explanation.
- **Implementation_File** — Any source, configuration, or scaffolding file in the MatchLayer repository that a Topic_Doc references by path.

## Requirements

### Requirement 1: Learning_Docs_Library structure and extensibility

**User Story:** As a junior developer returning to the project, I want one obvious place that holds long-form learning content for every phase, so that I never have to hunt across the repo for explanations.

#### Acceptance Criteria

1. THE Learning_Docs_Library SHALL be rooted at the repository-relative path `docs/learning/` and SHALL be created as a directory at that exact path.
2. THE Learning_Docs_Library SHALL contain a Library_Index file at the exact path `docs/learning/README.md` as a Markdown file with a `.md` extension.
3. THE Learning_Docs_Library SHALL contain a Conventions_Doc file at the exact path `docs/learning/CONVENTIONS.md` as a Markdown file with a `.md` extension.
4. THE Library_Index SHALL contain a section titled `Phase Sub-Libraries` that lists every direct child directory of `docs/learning/` whose name matches the pattern `phase-<N>/` (where `<N>` is an integer between 1 and 7 inclusive), and each list entry SHALL include the sub-library directory name and a relative Markdown link to that directory, including an entry for the Phase_1_Sub_Library at `docs/learning/phase-1/`.
5. THE Library_Index SHALL contain a section titled `Reader Assumption` whose body states verbatim that the intended Reader is "a junior developer with zero prior knowledge of the topic".
6. WHERE a new phase sub-library is added under `docs/learning/`, THE Learning_Docs_Library SHALL place it as a new sibling directory directly under `docs/learning/` and SHALL leave every file and subdirectory under `docs/learning/phase-1/` byte-identical to its pre-addition state.
7. THE Library_Index SHALL contain a section titled `External Sources` that lists at minimum one relative Markdown link to `.kiro/steering/`, one relative Markdown link to `docs/adr/`, and one relative Markdown link to each per-app README under `apps/*/README.md` that exists at the time of authoring.
8. IF any file under `docs/learning/` (excluding the Library_Index and the Conventions_Doc themselves) reproduces a contiguous block of 50 or more words from any file under `.kiro/steering/`, `docs/adr/`, or any `apps/*/README.md`, THEN THE Learning_Docs_Library SHALL be considered non-compliant with the no-duplication rule and the duplicated content SHALL be replaced by a relative Markdown link to the original source.

### Requirement 2: Phase_1_Sub_Library and Phase_1_Index

**User Story:** As a junior developer reading the Phase 1 implementation for the first time, I want a single landing page that lists every Phase 1 topic with a one-line summary, so that I can navigate to whatever I do not understand without scrolling through every document.

#### Acceptance Criteria

1. THE Phase_1_Sub_Library SHALL be a directory rooted at the repository-relative path `docs/learning/phase-1/` and SHALL exist as a tracked directory in the repository.
2. THE Phase_1_Sub_Library SHALL contain exactly one Phase_1_Index file located at `docs/learning/phase-1/README.md`.
3. THE Phase_1_Index SHALL include an introduction section, between 40 and 150 words in length, that explicitly references the Phase_1_Foundation_Spec by its path `.kiro/specs/phase-1-foundation/` and identifies it as the implementation source of truth for Phase 1.
4. THE Phase_1_Index SHALL contain one section heading per thematic section defined in the Phase_1_Topic_Coverage_List, in this exact order: foundation and tooling, frontend, backend, security, database and storage, containerization, contracts and codegen, hosting and deploy.
5. THE Phase_1_Index SHALL list every Topic_Doc that exists in the Phase_1_Sub_Library exactly once, placed under the single thematic section assigned to that Topic_Doc by the Phase_1_Topic_Coverage_List.
6. THE Phase_1_Index SHALL render each Topic_Doc entry as a Markdown hyperlink whose target is the Topic_Doc filename relative to `docs/learning/phase-1/` and whose link text is the Topic_Doc title.
7. THE Phase_1_Index SHALL include, immediately after each Topic_Doc hyperlink, a single-sentence summary between 8 and 30 words that describes what the Topic_Doc covers and ends with a period.
8. THE Phase_1_Index SHALL contain a section titled "Recommended reading order" that lists every Topic_Doc as an ordered (numbered) list, with the first entry belonging to the foundation and tooling section and the last entry belonging to the hosting and deploy section.
9. THE Phase_1_Sub_Library SHALL store every Topic_Doc as a single Markdown file placed directly inside `docs/learning/phase-1/` (no subdirectories), with a filename matching the regex `^[a-z0-9]+(-[a-z0-9]+)*\.md$` and excluding the reserved name `README.md`.
10. IF a Topic_Doc listed in the Phase_1_Topic_Coverage_List has no corresponding Markdown file in the Phase_1_Sub_Library, THEN THE Phase_1_Index SHALL omit that Topic_Doc from all sections and from the recommended reading order rather than rendering a broken hyperlink.

### Requirement 3: Doc_Template specification

**User Story:** As a junior developer reading any Topic_Doc, I want every document to follow the same section structure, so that I always know where to find the conceptual explanation, the MatchLayer-specific usage, the pitfalls, and the further reading.

#### Acceptance Criteria

1. THE Conventions_Doc SHALL define the Doc_Template as an ordered list of exactly seven required sections in this order: "Introduction", "Problem it solves", "Mental model", "How it works", "MatchLayer Phase 1 usage", "Common pitfalls", "External reading", and one optional section "Hands-on checkpoint" placed immediately before "External reading" when present.
2. THE Doc_Template SHALL require an "Introduction" section that introduces the topic in plain language and SHALL require it to contain an explicit statement of at least three learning outcomes the Reader will know after reading the Topic_Doc, each phrased as a single declarative sentence.
3. THE Doc_Template SHALL require a "Problem it solves" section that names at least one concrete problem the topic addresses and describes at least one prior approach or state that existed before the topic was adopted.
4. THE Doc_Template SHALL require a "Mental model" section that contains at least one of the following artifacts: a named analogy, a diagram rendered as an image or as text-based ASCII/Mermaid, or a numbered step-by-step walkthrough of at least three steps.
5. THE Doc_Template SHALL require a "How it works" section that explains the topic conceptually and SHALL prohibit references to MatchLayer-specific files, modules, or Phase 1 implementation details within this section.
6. THE Doc_Template SHALL require a "MatchLayer Phase 1 usage" section that contains at least one reference to an Implementation_File path written as a repository-relative path and at least one fenced code snippet whose contents are copied verbatim from that referenced Implementation_File.
7. THE Doc_Template SHALL require a "Common pitfalls" section that lists at least three distinct pitfalls, where each pitfall entry contains three labeled parts: the mistake, an observable symptom by which the Reader can recognize it, and a recovery action.
8. THE Doc_Template SHALL require an "External reading" section that lists at least one external resource drawn from one of these categories: official project documentation, a published book with author and title, or a canonical blog post with author and URL.
9. THE Doc_Template SHALL define "Hands-on checkpoint" as an optional section and SHALL require its inclusion only when a time-bounded exercise of no more than 30 minutes materially reinforces the topic, as judged against a written rubric in the Conventions_Doc.
10. WHERE a Topic_Doc author omits the "Hands-on checkpoint" section, THE Topic_Doc SHALL remain compliant with the Doc_Template provided all seven required sections are present in the specified order.
11. IF a Topic_Doc is missing any required Doc_Template section, contains the required sections in an order other than the one specified in criterion 1, or contains a "How it works" section that violates criterion 5, THEN THE Topic_Doc SHALL be marked non-compliant and SHALL be corrected before the spec is closed.
12. IF a Topic_Doc's "MatchLayer Phase 1 usage" section contains a code snippet that does not match the contents of the referenced Implementation_File at the time of authoring, THEN THE Topic_Doc SHALL be marked non-compliant and SHALL be corrected before the spec is closed.

### Requirement 4: Phase_1_Topic_Coverage_List

**User Story:** As a junior developer who explicitly asked for "everything" in Phase 1 to be explained, I want the spec to enumerate every required Topic_Doc, so that no concept used in Phase 1 is left unexplained.

#### Acceptance Criteria

1. THE Phase_1_Sub_Library SHALL contain at least one Topic_Doc for every entry enumerated in acceptance criteria 2 through 9 of this requirement, where coverage is verified by a one-to-one or many-to-one mapping from each entry to a Topic_Doc filename recorded in the Phase_1_Index.
2. THE Phase_1_Topic_Coverage_List SHALL include a "Foundation and tooling" section covering: monorepo concept and the apps-vs-packages split; pnpm and pnpm workspaces; uv as a Python package manager; Node.js version pinning via `.nvmrc` and Python version pinning via `.python-version`; the root `package.json` and `tsconfig.base.json`; `.editorconfig`; lockfiles (`pnpm-lock.yaml`, `uv.lock`) and the meaning of frozen-lockfile installs; `.env`, `.env.example`, and the env-drift detection script; pre-commit hooks (the framework, plus each hook used: `gitleaks`, `ruff format`, `ruff check --fix`, `prettier`, file hygiene checks).
3. THE Phase_1_Topic_Coverage_List SHALL include a "Frontend" section covering: Next.js App Router and the difference between Server Components and Client Components; TypeScript strict mode and the specific compiler options enabled in this repo; Tailwind CSS v4 and the `@theme inline` token strategy; shadcn/ui as a copy-in primitive library; Geist Sans and Geist Mono via `next/font`; Framer Motion and the reduced-motion accessibility pattern; `next-themes` and the system-default theme; the security-headers proxy (the Next.js 16 file-convention rename from `middleware.ts` to `proxy.ts`); WCAG AA color contrast in practice.
4. THE Phase_1_Topic_Coverage_List SHALL include a "Backend" section covering: FastAPI as an async ASGI framework and the application-factory pattern; Pydantic v2 and `pydantic-settings`; async Python and the asyncio model for the Reader; SQLAlchemy 2.x async engine, async session factory, and the per-request session dependency; connection pooling and the role of `pool_pre_ping`; Alembic migrations and the empty baseline strategy; structlog and structured JSON logging; the request-id ASGI middleware and the `X-Request-Id` header contract; the RFC 7807 error envelope; the OpenAPI dump CLI and how FastAPI generates an OpenAPI document.
5. THE Phase_1_Topic_Coverage_List SHALL include a "Security" section covering: the security headers set by the Next.js proxy and what each one defends against (CSP, HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy); CORS allowlists and why wildcard CORS is unsafe on authenticated endpoints; structured logging as a PII-defense tool and the redaction processor; secrets management, gitleaks, GitHub Secret Scanning, and `.env` discipline; dependency and supply-chain scanning (`pip-audit`, `pnpm audit --prod`, CodeQL, Dependabot security updates); the threat model categories listed in the security steering doc.
6. THE Phase_1_Topic_Coverage_List SHALL include a "Database and storage" section covering: PostgreSQL 16 fundamentals for the Reader (relational model, schemas, transactions, indexes); the difference between Postgres and MinIO and why Phase 1 uses both; Redis fundamentals and why Phase 1 stands it up even though it is unused until Phase 4; named Docker volumes and data persistence across `docker compose down`; the future addition of pgvector in Phase 2 and why Phase 1 stops short of it.
7. THE Phase_1_Topic_Coverage_List SHALL include a "Containerization" section covering: what a container is and how it differs from a virtual machine; Docker images, layers, and the build cache; Dockerfiles and multi-stage builds; `docker compose` as a multi-service local-development tool; healthchecks and the `--wait` flag; the production Dockerfiles in `infra/docker/` and what each instruction does; distroless base images, non-root users with high UIDs, the `--read-only` runtime contract, and why these matter for security; image digest pinning.
8. THE Phase_1_Topic_Coverage_List SHALL include a "Contracts and codegen" section covering: what OpenAPI is and how FastAPI generates it; the codegen orchestrator script and the role of `execa`; `openapi-typescript` and what it produces; `openapi-zod-client` and what it produces; the curated `index.ts` re-export pattern; the OpenAPI drift check in CI and why it exists.
9. THE Phase_1_Topic_Coverage_List SHALL include a "Hosting and deploy" section covering: GitHub Actions workflow structure (jobs, steps, triggers, concurrency, caching); the five CI jobs in this repo (`backend`, `frontend`, `shared-types`, `security`, `openapi-drift`) and what each one verifies; Dependabot configuration; branch protection rules and the required-checks aggregator job pattern; Vercel hobby tier as the Phase 1 frontend host; Fly.io as the Phase 1 backend host; AWS S3 as the Phase 1 file-storage backend; how Phase 1 hosting choices preserve the Phase 6 AWS migration path.
10. THE Phase_1_Index SHALL enumerate every entry from acceptance criteria 2 through 9 as a discrete row, where each row includes the entry text and the filename of the Topic_Doc that addresses it, such that a Reader can verify coverage by confirming no row is missing a Topic_Doc filename.
11. WHERE a single Topic_Doc addresses two or more entries from acceptance criteria 2 through 9, THE Phase_1_Sub_Library SHALL be permitted to consolidate those entries into that one Topic_Doc, provided that every consolidated entry is named in the Topic_Doc's introduction section and every consolidated entry is listed in the Phase_1_Index with that Topic_Doc's filename in its row.
12. IF any entry from acceptance criteria 2 through 9 has no Topic_Doc filename recorded against it in the Phase_1_Index, OR has a recorded Topic_Doc filename that does not exist in the Phase_1_Sub_Library, THEN THE Phase_1_Sub_Library SHALL be marked incomplete and the spec SHALL NOT be closed until every such entry maps to an existing Topic_Doc.

### Requirement 5: Beginner-accessibility content rules

**User Story:** As a junior developer who has never used Docker, JWT, async SQLAlchemy, or most of this stack, I want every Topic_Doc to assume I know nothing and build up from there, so that I can actually understand what I am reading.

#### Acceptance Criteria

1. THE Conventions_Doc SHALL state that every Topic_Doc is written for the Reader and SHALL define the Reader as a junior developer with no prior exposure to the topic, no prior exposure to its prerequisite topics, and no assumed familiarity with the project's chosen stack.
2. THE Conventions_Doc SHALL require every Topic_Doc to define every domain-specific term (any noun or noun phrase that is not part of general programming vocabulary, including framework names, protocol names, pattern names, and project-internal concepts) on first use within that Topic_Doc, even when the term is defined elsewhere in the Learning_Docs_Library, with the definition appearing in the same paragraph as the first use.
3. THE Conventions_Doc SHALL require every Topic_Doc to introduce every acronym in expanded form on first use within that Topic_Doc, formatted as "Expanded Form (ACRONYM)" (for example, "Cross-Origin Resource Sharing (CORS)"), and SHALL permit use of the bare acronym only after the first expanded occurrence in the same Topic_Doc.
4. THE Conventions_Doc SHALL require every Topic_Doc to contain a mental-model section that appears before any technical detail and SHALL require that section to include at least one of the following concrete handholds: an analogy to a familiar real-world concept, a diagram, or a numbered step-by-step walkthrough.
5. THE Conventions_Doc SHALL prohibit, in every Topic_Doc, the following knowledge-presuming constructions: "as you know", "obviously", "clearly", "simply", "just", "of course", "everyone knows", "it should be clear", and any phrase asserting that the Reader already understands a concept that has not been defined earlier in the same Topic_Doc.
6. THE Conventions_Doc SHALL require every Topic_Doc to declare its prerequisite Topic_Docs in the introduction as an explicit list, with each prerequisite hyperlinked to the corresponding Topic_Doc, where a prerequisite is defined as any Topic_Doc covering a concept that the current Topic_Doc references without defining inline.
7. THE Conventions_Doc SHALL require every Topic_Doc, when referencing a concept covered in another Topic_Doc, to include either a brief inline definition of one to three sentences or a hyperlink to the relevant Topic_Doc, and SHALL prohibit any reference to such a concept that lacks both.
8. THE Conventions_Doc SHALL require the Topic_Doc author, before marking a Topic_Doc as ready for review, to evaluate every section of that Topic_Doc against criteria 1 through 7 of this requirement and to revise any section that fails any criterion.
9. IF a Topic_Doc under review contains a term, acronym, prerequisite reference, or jargon that does not satisfy criteria 2, 3, 6, or 7, THEN THE Conventions_Doc SHALL require the reviewer to reject the Topic_Doc and cite the specific failing criterion.

### Requirement 6: File-reference and code-snippet accuracy

**User Story:** As a junior developer trying to map a concept to the actual codebase, I want every file path and code snippet in a Topic_Doc to match the real repository, so that I can open the file and see what the document is talking about.

#### Acceptance Criteria

1. THE Conventions_Doc SHALL require every reference to an Implementation_File in a Topic_Doc to use the file's POSIX-style path relative to the repository root, with no leading slash and no `./` prefix (for example, `apps/api/src/matchlayer_api/main.py`).
2. WHEN a Topic_Doc includes a code snippet copied from an Implementation_File, THE Conventions_Doc SHALL require the snippet to be reproduced character-for-character from the current state of that Implementation_File on the branch where the Topic_Doc is committed, with no edits other than removing whole lines.
3. WHERE a Topic_Doc includes a code snippet that is not a verbatim copy of an Implementation_File, THE Conventions_Doc SHALL require the snippet to be preceded by a labelling line containing the exact phrase "simplified for illustration" and followed by a Markdown hyperlink whose target is the repository-root-relative path of the source Implementation_File.
4. IF a Topic_Doc references an Implementation_File path that does not exist in the repository on the branch where the Topic_Doc is committed, THEN THE Topic_Doc SHALL be marked non-compliant and SHALL block closure of the spec until every such reference resolves to an existing file in the same repository.
5. WHEN a pull request modifies an Implementation_File that is referenced by one or more Topic_Doc files, THE pull request SHALL, within the same pull request and before merge, update each affected Topic_Doc so that every reference to that Implementation_File still resolves and every verbatim snippet copied from that Implementation_File matches the post-merge file content character-for-character.
6. THE Conventions_Doc SHALL require every code snippet in a Topic_Doc to be enclosed in a Markdown fenced code block opened and closed with three backticks and tagged on the opening fence with exactly one lowercase language identifier from the set {python, typescript, tsx, javascript, jsx, yaml, json, dockerfile, sql, bash, sh, text}.
7. THE Conventions_Doc SHALL require every code snippet copied from an Implementation_File to be immediately preceded or followed by a single citation line of the form ``Source: `<path>` `` where `<path>` is the repository-root-relative POSIX path of the source Implementation_File.

### Requirement 7: Internal and external link integrity

**User Story:** As a junior developer following a hyperlink in a Topic_Doc, I want both internal links and external links to resolve to the right place, so that the documentation is trustworthy.

#### Acceptance Criteria

1. THE Conventions_Doc SHALL state that every internal hyperlink in a Topic_Doc MUST use a relative path (no leading scheme, no leading host, no leading "/") that resolves to an existing file or heading anchor in the repository when interpreted from the directory containing the Topic_Doc.
2. IF an internal hyperlink in a Topic_Doc resolves to a path that does not exist in the repository at the commit in which the Topic_Doc is introduced or modified, THEN THE Topic_Doc SHALL be marked non-compliant, the broken link SHALL be reported with its source file, line number, and target path, and the spec containing the Topic_Doc SHALL NOT be closed until every such link resolves to an existing path.
3. IF an internal hyperlink in a Topic_Doc points at a heading anchor (a fragment beginning with "#") that does not match a heading present in the target file at the commit in which the Topic_Doc is introduced or modified, THEN THE Topic_Doc SHALL be marked non-compliant and SHALL be corrected before the spec containing the Topic_Doc is closed.
4. THE Conventions_Doc SHALL state that every external hyperlink in a Topic_Doc MUST use the "https://" scheme exactly, and SHALL state that hyperlinks using "http://", protocol-relative ("//"), or any other scheme are non-compliant.
5. THE Conventions_Doc SHALL define an authoritative source for an external hyperlink as one of: the official documentation site of the referenced product, library, or service; the official specification published by its standards body; the project's own first-party website; or a canonical reference site explicitly listed in the Conventions_Doc (the list SHALL include at minimum MDN Web Docs, the Python documentation at docs.python.org, and the Next.js documentation at nextjs.org), and SHALL state that when an authoritative source exists for a topic, the Topic_Doc MUST link to it instead of a secondary tutorial, blog post, or aggregator.
6. THE Conventions_Doc SHALL require every Topic_Doc to contain a section whose heading is exactly "External reading" and SHALL require that section to list at least one and at most ten external hyperlinks, each of which satisfies acceptance criteria 4 and 5.

### Requirement 8: Naming, style, and template compliance

**User Story:** As a junior developer browsing the `docs/learning/phase-1/` folder, I want filenames and headings to follow a consistent pattern, so that I can predict what is in each file before opening it.

#### Acceptance Criteria

1. THE Conventions_Doc SHALL require every Topic_Doc filename inside a phase sub-library to use kebab-case, where the filename matches the regular expression `^[a-z0-9]+(-[a-z0-9]+)*\.md$` (for example, `docker-and-compose.md`, `fastapi-application-factory.md`), with a maximum filename length of 80 characters including the `.md` extension.
2. THE Conventions_Doc SHALL require every Topic_Doc to begin on line 1 with exactly one H1 heading (`# `) whose text is the human-readable title of the topic, contains between 3 and 80 characters, and is followed by at least one blank line before any other content.
3. THE Conventions_Doc SHALL require every Topic_Doc section that maps to a Doc_Template section to use an H2 heading (`## `) whose text matches the section name defined in the Doc_Template exactly, including capitalization, spacing, and punctuation.
4. THE Conventions_Doc SHALL require the required Doc_Template sections to appear in this exact order with no omissions, additions, or reordering: Introduction, Problem it solves, Mental model, How it works, MatchLayer Phase 1 usage, Common pitfalls, External reading.
5. WHERE the optional Hands-on checkpoint section is included, THE Topic_Doc SHALL place its H2 heading immediately after the last content line of the Common pitfalls section and immediately before the H2 heading of the External reading section, with no other H2 sections between them.
6. IF a Topic_Doc filename does not match the kebab-case pattern defined in criterion 1, THEN THE Topic_Doc SHALL be considered non-compliant, the spec SHALL NOT be closed, and the violation SHALL be surfaced with an indication identifying the offending filename and the rule it violates.
7. IF a Topic_Doc is missing a required Doc_Template section, contains a required section out of the order defined in criterion 4, or uses a heading whose text does not match the Doc_Template section name exactly, THEN THE Topic_Doc SHALL be considered non-compliant, the spec SHALL NOT be closed, and the violation SHALL be surfaced with an indication identifying the offending Topic_Doc and the specific rule it violates.

### Requirement 9: Maintenance discipline

**User Story:** As a junior developer who returns to the project months later, I want the learning docs to stay in sync with the code, so that I never read an explanation that no longer matches reality.

#### Acceptance Criteria

1. THE Conventions_Doc SHALL state that any change to an Implementation_File or to a library or technology covered by a Topic_Doc must include the corresponding Topic_Doc update in the same pull request before merge.
2. WHEN a pull request introduces a new Implementation_File covered by a Phase 1 topic, adds a new library or technology referenced by an existing Topic_Doc, or replaces a library or technology referenced by an existing Topic_Doc, THE pull request SHALL update or add the corresponding Topic_Doc in the same pull request before merge.
3. WHEN a pull request removes an Implementation_File covered by a Topic_Doc or removes a library or technology covered by a Topic_Doc, THE pull request SHALL update or remove the corresponding Topic_Doc in the same pull request before merge.
4. IF a pull request meets the conditions in criterion 2 or 3 but does not include the corresponding Topic_Doc change, THEN THE pull request SHALL be blocked from merging until either the Topic_Doc change is added or a written waiver naming the responsible reviewer is recorded in the pull request description.
5. THE Conventions_Doc SHALL identify the spec author by name or handle as the maintainer of the Phase_1_Sub_Library and SHALL state that maintainership transfers only via a future spec that names the new maintainer by name or handle.
6. THE Conventions_Doc SHALL require any future phase sub-library to name its maintainer by name or handle in the Conventions_Doc before that sub-library is merged.

### Requirement 10: Non-goals and explicit exclusions

**User Story:** As a junior developer or future contributor, I want the Learning_Docs_Library to make clear what it is not, so that I do not duplicate content that already lives elsewhere or expect content the library does not provide.

#### Acceptance Criteria

1. THE Library_Index SHALL contain a section titled "Non-goals" or "What this is not" that contains at least four bullet points, one for each exclusion described in criteria 2 through 5.
2. THE Library_Index SHALL state that the Learning_Docs_Library is not an auto-generated API reference and SHALL include a relative link to the committed OpenAPI document, identified as the source of API reference truth.
3. THE Library_Index SHALL state that the Learning_Docs_Library is not a substitute for any per-app README and SHALL include a relative link to each README under `apps/` (one link per app present in the repo at the time the Library_Index is written).
4. THE Library_Index SHALL state that the Learning_Docs_Library is not the same as the Architecture Decision Records and SHALL include a relative link to `docs/adr/` so the Reader knows where to find decision rationale.
5. THE Library_Index SHALL state that the Learning_Docs_Library is the long-form companion to the steering documents in `.kiro/steering/` and SHALL include a relative link to that folder.
6. THE Phase_1_Sub_Library SHALL NOT contain any page, section, or heading whose subject is a phase numbered 2 or higher, except for sentences that explicitly explain how a Phase 1 choice preserves a future option, which SHALL be limited to at most two sentences per page and SHALL NOT include implementation guidance for the future phase.
7. IF a link target referenced by the Library_Index under criteria 2 through 5 does not exist in the repository at the path given, THEN THE Library_Index SHALL be treated as failing validation and the link SHALL be flagged as broken with the missing path identified.
