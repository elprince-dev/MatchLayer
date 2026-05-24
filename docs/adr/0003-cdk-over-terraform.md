# 0003 — IaC: AWS CDK (TypeScript) over Terraform

**Status:** Accepted
**Date:** 2026-05-23
**Applies to:** Phase 6+

## Context

Phase 6 productionizes MatchLayer on AWS. Every resource must be defined in code. Two main options:

- **Terraform** — declarative HCL, multi-cloud, industry standard, large ecosystem, separate state file managed by the team.
- **AWS CDK** — write infra in TypeScript/Python, synthesizes to CloudFormation, AWS manages state.

## Decision

**AWS CDK in TypeScript**, in `infra/cdk/`.

## Rationale

- **AWS-only deployment.** Terraform's multi-cloud strength is irrelevant. CDK's AWS-first design wins: new AWS services land in CDK first, L2/L3 constructs are higher-level than Terraform community modules.
- **Solo dev already familiar with CDK.** Phase 6's goal is "productionize", not "learn IaC from scratch".
- **No separate state file.** CloudFormation manages state. No S3 + DynamoDB locking infrastructure to set up and operate.
- **Higher-level constructs.** A Fargate service in CDK is ~15 lines; in Terraform it's 100+ unless we adopt a community module.
- **TypeScript over Python** for CDK so `infra/` shares language and tooling with `apps/web/` and `packages/`. Consistent with the rest of the JS workspace.

## Consequences

**Positive**
- Faster onboarding to Phase 6.
- Type-safe constructs catch most config errors at compile time.
- Tooling (linting, formatting) shared with frontend code.
- CloudFormation drift detection works out of the box.

**Negative**
- Slower than Terraform for stack-level operations on large stacks (CloudFormation is the bottleneck).
- AWS-locked. If MatchLayer ever needs another cloud, full rewrite.
- Resume signal slightly weaker than Terraform — CDK is widely used but Terraform appears in more job postings. Mitigation: a small Terraform side-project later if needed.

## Alternatives considered

- **Terraform:** rejected for reasons above.
- **AWS CDK in Python:** considered. Would share language with the API. Rejected because most of the JS toolchain (eslint, prettier, tsconfig) wouldn't apply, and `infra/` would feel orphaned from the rest of the workspace.
- **Pulumi:** considered. Similar to CDK but multi-cloud. Rejected for the same reason as Terraform — multi-cloud isn't a real requirement.
- **Click-ops in the AWS console:** never an option for production.
