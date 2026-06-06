# MatchLayer Learning Docs

The Learning Docs Library is the long-form, teach-from-scratch companion to the MatchLayer codebase. It explains, from first principles, every technology, library, design choice, and convention the project uses — one Markdown document per topic, grouped into per-phase sub-libraries. Where the steering docs give the always-loaded summary and the Architecture Decision Records capture the rationale behind a decision, this library is the place to actually learn how each piece works and why it is here.

## Reader Assumption

Every document in this library is written for one reader: a junior developer with zero prior knowledge of the topic. Nothing about the stack is taken for granted. Terms are defined on first use, acronyms are expanded before they are abbreviated, and each topic builds up from a plain-language mental model before any code appears. If you have never touched the tool a document covers, that document was written for you.

## Phase Sub-Libraries

MatchLayer is built in seven progressive phases. Each phase that has learning content gets its own sub-library directory directly under `docs/learning/`, added as a sibling without disturbing the earlier phases. The sub-libraries present today:

- [phase-1/](phase-1/) — MVP foundation: the monorepo and tooling, the FastAPI and Next.js scaffold, Docker and Compose, CI, authentication and accounts, and the deterministic resume-to-job matching surface.

## External Sources

This library links out to the canonical sources rather than restating them. Reach for these when you want the authoritative version of something this library only teaches around:

- [`.kiro/steering/`](../../.kiro/steering/) — the always-loaded steering documents (product, tech stack, repo structure, conventions, security, SEO, and design).
- [`docs/adr/`](../adr/) — the Architecture Decision Records that record why each significant decision was made.
- [`apps/api/README.md`](../../apps/api/README.md) — how to run, test, and develop the FastAPI backend.
- [`apps/web/README.md`](../../apps/web/README.md) — how to run, test, and develop the Next.js frontend.

## Non-goals

This library is deliberately scoped. It is not a stand-in for any of the references below, and it links to each one instead of duplicating it:

- **Not an auto-generated API reference.** The API contract is produced from the live FastAPI application by [`apps/api/src/matchlayer_api/tools/dump_openapi.py`](../../apps/api/src/matchlayer_api/tools/dump_openapi.py), which emits the OpenAPI document (`openapi.json`) that codegen consumes. That generated document is the source of API reference truth; it is a transient build artifact, regenerated on demand rather than committed, so this list links to the committed generator that defines it.
- **Not a replacement for the backend README.** For backend setup and run instructions, see [`apps/api/README.md`](../../apps/api/README.md).
- **Not a replacement for the frontend README.** For frontend setup and run instructions, see [`apps/web/README.md`](../../apps/web/README.md).
- **Not the Architecture Decision Records.** Decision rationale lives in [`docs/adr/`](../adr/); this library teaches the concepts, it does not record the decisions.
- **The long-form companion to the steering docs.** This library expands on the always-loaded summaries in [`.kiro/steering/`](../../.kiro/steering/); it complements them and does not replace them.
