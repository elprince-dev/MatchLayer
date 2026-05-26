---
inclusion: manual
---

# Phase 6 — AWS Production Architecture

**Status:** not started
**Depends on:** Phase 5 shipped.
**Goal:** move from "deployed somewhere on a free tier" to "real production architecture on AWS" with CI/CD, observability, and a path to scale.

## Why this phase exists
The product works. Now it needs to look and behave like a real production system: containerized backend on ECS, frontend on CloudFront, async work on SQS, logs and metrics in CloudWatch, infrastructure as code, deploys triggered by git.

## In scope
- **Compute**
  - **ECS Fargate** for backend API and agent worker. Two services in one cluster.
  - **Vercel or CloudFront + S3** for the frontend. Vercel is simpler; CloudFront is more impressive on a resume. Default to **Vercel for speed, plan a migration runbook to CloudFront**.
- **Data**
  - **RDS Postgres 16** with pgvector. `db.t4g.micro` or `db.t4g.small` — enough for the workload, cheap. Multi-AZ off in dev/staging, on in prod.
  - **ElastiCache Redis** — minimum tier (`cache.t4g.micro`). Used for caching and rate-limit counters.
- **Async**
  - **SQS** queue for agent jobs (already introduced in Phase 4 via LocalStack — now real).
  - **SQS DLQ** for failed jobs after 3 retries.
- **Storage**
  - **S3** for resume files (already used). Add: lifecycle rule moving objects to Glacier after 90 days.
- **Observability**
  - **CloudWatch Logs** ingesting structured JSON logs from all containers.
  - **CloudWatch Metrics** + **CloudWatch Alarms** for: error rate, latency p95, queue depth, RDS connections, LLM cost spikes.
  - **AWS X-Ray** OR **CloudWatch ServiceLens** as the OTel collector backend (whichever is simpler — likely ServiceLens).
  - **Sentry** for application-level error aggregation (frontend + backend).
- **Networking & security**
  - VPC with public + private subnets. Backend in private, ALB in public. NAT in public; consider VPC endpoints (S3, ECR, Secrets Manager) to reduce NAT egress cost.
  - **Secrets Manager** for all secrets (DB password, JWT signing key, OpenAI key, Stripe keys when Phase 7 lands). Managed rotation for DB password.
  - **KMS customer-managed keys** for resume S3 bucket and RDS storage. Default AWS-managed keys only for non-Restricted resources.
  - **S3 Block Public Access** enabled at the account level *and* the bucket level. Resume bucket has versioning + default SSE-KMS + 90-day Glacier lifecycle + public-access denied.
  - **WAF** on the ALB with AWS managed rule sets (Core, Known Bad Inputs, IP reputation, rate-based).
  - **HTTPS everywhere** via ACM. HTTP → HTTPS redirect at the ALB.
  - **CloudTrail** in all regions logging to a dedicated logging-account-style S3 bucket with object lock.
  - **GuardDuty** enabled.
  - **AWS Config** with the security best-practices managed rule pack.
  - **VPC Flow Logs** to CloudWatch.
  - **IAM least privilege** as a project-wide principle. Per-service roles. No `*:*`. No long-lived IAM users for humans (SSO/Identity Center only). Per-environment deploy roles.
  - **GitHub Actions → AWS via OIDC federation.** No long-lived AWS access keys in GitHub Secrets. Per-repo trust policy with branch + environment conditions.
  - **ECR scan-on-push** enabled. Block deployment of images with critical CVEs (gate in CI, not just advisory).
  - **Container hardening:** non-root user, read-only root filesystem, minimal base image (distroless or chiseled), no shell in prod images.
  - **Backups:** RDS automated daily, 7-day retention staging / 30-day retention prod. Quarterly restore test documented in runbook.
- **CI/CD**
  - **GitHub Actions** pipelines:
    - PR: lint, type-check, test, **`pip-audit`, `pnpm audit --prod`, CodeQL, Trivy on built image, gitleaks**, eval-subset.
    - Merge to `main`: build images → ECR scan-on-push → deploy to staging → smoke tests → manual approval → deploy to prod.
  - **Branch protection** on `main`: required PR reviews (self-review for solo dev), required status checks, no force push, require linear history.
  - **OIDC** assumed-role for AWS auth. Short-lived credentials only.
  - Image tags by short SHA + commit timestamp. Rollback = redeploy previous tag.
- **IaC**
  - **AWS CDK in TypeScript**, in `infra/cdk/`.
  - One stack per environment (`MatchLayer-Staging`, `MatchLayer-Prod`).
  - Constructs reused across environments.

## Explicitly out of scope
- Multi-region deployment.
- Self-hosted Kubernetes (Fargate is the right call).
- Service mesh.
- Auto-scaling tuned to traffic patterns (start with sensible defaults, optimize when there's traffic).
- Blue/green or canary deploys (rolling is fine for now).
- Infrastructure for Phase 7 (Stripe webhooks, etc.) — add when Phase 7 lands.

## Deliverables
1. CDK app deploying staging + prod stacks.
2. ECR repos and ECS services live.
3. RDS + ElastiCache provisioned.
4. SQS queue + DLQ wired up.
5. GitHub Actions deploying on merge.
6. CloudWatch dashboard linked from the admin panel.
7. Runbook (`docs/runbooks/incident-response.md`).
8. ADR (`0006-aws-architecture.md`).

## Success criteria
- Deploy from `git push` to production live in < 15 minutes (post-approval).
- Cold-start of API container < 30 seconds.
- All sensitive values come from Secrets Manager; nothing in env-vars-as-cleartext.
- p95 API latency in prod < 1s for non-LLM endpoints, < 8s for LLM endpoints.
- Documented monthly cost estimate. Stay under $100/month with low usage.
- AWS Config compliance score for the security baseline pack > 90%.
- All container images pass ECR scan with no critical CVEs at deploy time.
- Zero long-lived AWS access keys associated with the GitHub repo.

## Skills demonstrated
AWS · ECS Fargate · RDS · SQS · CloudWatch · CDK · GitHub Actions · CI/CD · cloud security · cost control · observability

## Risks & gotchas
- **Cost surprise.** Always check the billing dashboard the morning after a deploy. NAT Gateway is the silent killer (~$33/mo for `nat-gateway-1`). Consider VPC endpoints for ECR/S3 to reduce NAT traffic.
- **Cold starts.** Fargate cold starts are slower than Lambda. Keep at least 1 task warm in prod.
- **Migrations on deploy.** Run Alembic migrations as a one-shot ECS task before the new app version goes live, not in app startup. Document the order.
- **Vercel ↔ CloudFront tension.** If you start on Vercel, the frontend deploys outside CDK. Document this clearly so it doesn't drift.
- **CDK bootstrap.** Forgetting `cdk bootstrap` in a new account/region wastes an hour. Document it in the runbook.
- **Secrets in CDK.** Don't hard-code secrets in CDK. Reference Secrets Manager ARNs.

## Folder additions
```
infra/cdk/
  bin/matchlayer.ts                 # CDK app entrypoint
  lib/
    network-stack.ts
    data-stack.ts                   # RDS, Redis, S3
    compute-stack.ts                # ECS, ALB, ECR
    queue-stack.ts                  # SQS + DLQ
    observability-stack.ts
  cdk.json
  package.json
  tsconfig.json
.github/workflows/
  deploy-staging.yml
  deploy-prod.yml
docs/runbooks/
  incident-response.md
  deploy.md
  rollback.md
docs/adr/0006-aws-architecture.md
```

## Work breakdown
1. Bootstrap CDK in `infra/cdk/`, deploy a hello-world stack to staging.
2. Set up GitHub OIDC trust + per-repo deploy role. Verify via a no-op CI run.
3. Build network-stack: VPC, subnets, security groups, VPC Flow Logs, VPC endpoints for S3/ECR/Secrets Manager.
4. Build data-stack: RDS (KMS-encrypted), Redis, S3 bucket (Block Public Access + SSE-KMS + lifecycle). Run a manual migration end-to-end.
5. Build compute-stack: ECR with scan-on-push, ECS cluster, API service, worker service, ALB with WAF.
6. Build queue-stack: SQS + DLQ, IAM roles for worker.
7. Wire Secrets Manager for DB password, JWT signing key, OpenAI key. Configure managed rotation where supported.
8. Build observability-stack: CloudTrail, GuardDuty, AWS Config, CloudWatch dashboards, alarms.
9. Add Sentry to frontend and backend.
10. CI workflow: lint/test/type-check + `pip-audit` + `pnpm audit --prod` + CodeQL + Trivy + gitleaks. Build + push images on merge.
11. CD workflow: trigger ECS service update with new image tag. Pre-deploy migration step as one-shot ECS task.
12. Container hardening: non-root user, read-only fs, distroless base. Verify with Trivy + manual checks.
13. Run a load test (~10 concurrent users) against staging; tune ECS task sizing.
14. Write deploy + rollback + incident runbooks.
15. Cut over from the Phase 1–5 free-tier deployment to AWS, keep DNS pointing only after smoke tests pass.
16. Write the ADR.

## Definition of done
Every push to `main` runs CI, deploys to staging automatically, and to prod on approval. The system runs entirely on AWS, observability is wired, secrets are properly managed, and there's a documented rollback path if anything breaks.
