# Architecture Decision Records — index

Each ADR captures the long-form rationale for a significant decision. Full text in `docs/adr/NNNN-*.md`. Once accepted, ADRs are immutable; superseding decisions get a new ADR.

When proposing or implementing changes, check this index first to avoid contradicting accepted decisions. If a change requires overturning an ADR, write a new ADR explaining why.

## Accepted

- **0001 — Phasing strategy.** 7 progressive phases, each deployable, no early-building of future phases.
- **0002 — Monorepo layout.** Single repo, `apps/` + `packages/` + top-level `ml/` and `infra/`.
- **0003 — IaC: AWS CDK (TypeScript) over Terraform.** Solo dev familiarity, AWS-only deploy, no separate state file.
- **0004 — Vector storage: pgvector over a dedicated vector DB.** One fewer service, sufficient at our scale.
- **0005 — Package managers: pnpm + uv.** Strict isolation, monorepo workspaces, fast installs.
- **0006 — SEO strategy and the public/authenticated indexing split.** Public marketing pages get full SEO; authenticated PII pages are never indexed (`noindex`, robots-disallowed, out of sitemap). JSON-LD via CSP nonce, deferred in Phase 1. See `seo.md`.

## Conventions

- ADRs are numbered sequentially.
- New ADR template: Status, Date, Context, Decision, Rationale, Consequences, Alternatives.
- Amend the index above when an ADR is added or superseded.
