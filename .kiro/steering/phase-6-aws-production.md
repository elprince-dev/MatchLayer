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
  - VPC with public + private subnets. Backend in private, ALB in public.
  - **Secrets Manager** for all secrets (DB password, JWT secret, OpenAI key, Stripe keys when Phase 7 lands).
  - WAF on the ALB with basic managed rule sets.
  - HTTPS everywhere via ACM.
- **CI/CD**
  - **GitHub Actions** pipelines:
    - PR: lint, test, type-check, eval-subset.
    - Merge to `main`: build images → push to ECR → deploy to staging → run smoke tests → manual approval → deploy to prod.
  - Image tags by short SHA. Rollback = redeploy previous tag.
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
2. Build network-stack: VPC, subnets, security groups.
3. Build data-stack: RDS, Redis, S3 bucket. Run a manual migration end-to-end.
4. Build compute-stack: ECR, ECS cluster, API service, worker service, ALB.
5. Build queue-stack: SQS + DLQ, IAM roles for worker.
6. Wire Secrets Manager for DB password, JWT, OpenAI key.
7. CI workflow: build + push images on merge.
8. CD workflow: trigger ECS service update with the new image tag.
9. Set up CloudWatch dashboards and a few critical alarms.
10. Add Sentry to frontend and backend.
11. Run a load test (~10 concurrent users) against staging; tune ECS task sizing.
12. Write deploy + rollback + incident runbooks.
13. Cut over from the Phase 1–5 free-tier deployment to AWS, keep DNS pointing only after smoke tests pass.
14. Write the ADR.

## Definition of done
Every push to `main` runs CI, deploys to staging automatically, and to prod on approval. The system runs entirely on AWS, observability is wired, secrets are properly managed, and there's a documented rollback path if anything breaks.
