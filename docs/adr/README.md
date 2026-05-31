# Architecture Decision Records

ADRs capture the long-form rationale for non-obvious architectural decisions. The summary versions live in `.kiro/steering/`; the rationale lives here.

## Format

Each ADR follows: Status, Date, Context, Decision, Consequences, Alternatives. Numbered sequentially. **Once accepted, ADRs are immutable.** Superseded ADRs get a new ADR with status `Supersedes NNNN`.

## Index

- [0001 — Phasing strategy](./0001-phasing-strategy.md)
- [0002 — Monorepo layout](./0002-monorepo-layout.md)
- [0003 — IaC: AWS CDK over Terraform](./0003-cdk-over-terraform.md)
- [0004 — Vector storage: pgvector over a dedicated vector DB](./0004-pgvector-vs-dedicated-vector-db.md)
- [0005 — Package managers: pnpm + uv](./0005-package-managers.md)
- [0006 — SEO strategy and the public/authenticated indexing split](./0006-seo-and-indexing-policy.md)
