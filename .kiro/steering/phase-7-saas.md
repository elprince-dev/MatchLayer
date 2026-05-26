---
inclusion: manual
---

# Phase 7 — SaaS & Advanced Features

**Status:** not started
**Depends on:** Phase 6 shipped.
**Goal:** turn MatchLayer into a real SaaS product. Subscriptions, multi-tenancy primitives, resume versioning, admin tooling, and a path to recruiter/team mode.

## Why this phase exists

Up to Phase 6, MatchLayer is a free tool with production-grade infra. Phase 7 is the leap to "could plausibly be a startup". This phase signals product thinking on top of engineering depth.

## In scope

- **Subscriptions (Stripe)**
  - Plans: Free (1 resume, 5 matches/month, basic coach), Pro ($X/mo, unlimited matches + full coach + interview prep), Team ($Y/mo, deferred).
  - Stripe Checkout for sign-up, Customer Portal for plan management.
  - Stripe webhooks → update subscription state in our DB.
  - **Webhook signature verification** via `Stripe-Signature` header — reject any payload that fails. Use Stripe's official SDK helper, not custom HMAC code.
  - **Webhook idempotency** via `stripe_events.stripe_event_id` unique index. Replays are safe.
  - Feature flags driven by subscription tier (`user.tier` checked at the service layer, not in routes).
- **Multi-factor authentication (MFA)**
  - TOTP-based MFA via authenticator apps. Optional for Free, encouraged for Pro, required for Team admins.
  - Recovery codes generated at enrollment, displayed once, hashed in DB.
  - MFA challenge required for sensitive actions (plan changes, account deletion, team admin changes).
- **Resume versioning**
  - Each resume has versions (v1, v2, …) with a `parent_resume_id`.
  - Diff view: side-by-side comparison of two versions.
  - "Apply coach suggestions" button creates a new version with selected rewrites applied.
- **Multi-user analytics (per user, not per workspace yet)**
  - Personal dashboard: matches over time, average score, score trend, top missing skills across applications.
  - Coach engagement: how often suggestions are accepted.
- **Admin dashboard**
  - User list, search, plan management, ban/disable.
  - System health: queue depth, eval scores, LLM cost trends.
  - Manual prompt rollback (uses the Phase 5 versioning).
- **Team / company mode (lightweight)**
  - Workspaces: a Team plan creates a workspace, invites members.
  - Shared resume library, role-based access (admin / member).
  - Note: full recruiter-side workflows (candidate pipelines, ATS export, scoring panels) deferred to a Phase 8 if pursued.
- **Compliance + lifecycle**
  - GDPR-style data export endpoint per user.
  - Account deletion → soft-delete + 30-day purge job.
  - Privacy policy + ToS pages (real lawyer review out of scope; templates fine).

## Explicitly out of scope (still)

- Mobile apps.
- Browser extensions.
- LinkedIn/job-board scraping.
- White-labeling.
- Marketplaces / third-party integrations.

## Deliverables

1. Stripe integration end-to-end with webhooks reconciling state.
2. Resume versioning with diff + "apply suggestions" flow.
3. Personal analytics dashboard.
4. Admin dashboard with user, plan, and system controls.
5. Team mode MVP: invite, role check, shared library.
6. ADR (`0007-saas-and-billing.md`).
7. Updated landing page with pricing.

## Success criteria

- A user can subscribe, downgrade, cancel, and re-subscribe without manual intervention.
- Stripe webhook handler is idempotent (replayed events don't double-charge state).
- Tier checks happen in one place at the service layer, not scattered across routes.
- Admin actions are audit-logged.
- A team admin can invite a member and they can immediately use the shared library.

## Skills demonstrated

SaaS architecture · Stripe · webhook handling · multi-tenancy · feature flags · analytics · admin tooling · GDPR basics · product engineering

## Risks & gotchas

- **Stripe complexity.** Subscriptions are deceptively complex (proration, trial-to-paid, mid-cycle upgrades, failed payments, dunning). Use the Stripe Customer Portal as much as possible to offload UX. Don't reimplement what Stripe gives you for free.
- **Webhook signature.** Always verify the `Stripe-Signature` header before parsing the body. Unverified webhooks are an unauthenticated mutation endpoint — a real attack vector.
- **Webhook idempotency.** Stripe will redeliver. Persist `stripe_event_id` (unique index) and reject duplicates _after_ signature verification.
- **Tier check leakage.** Easy to scatter `if user.tier == 'pro'` everywhere. Centralize in a `Permissions` service. One bug there = all tiers break — but that's the point: one place to test.
- **MFA recovery code handling.** Display once, hash in DB. Never log them. Be explicit in support flows: lost recovery codes = identity verification before reset.
- **Cross-tenant data leakage in team mode.** Every query touching team-owned data must scope by `team_id`. Add a SQLAlchemy event hook or middleware enforcing this where possible.
- **Team mode scope creep.** It's tempting to make team mode a full recruiter platform. Resist. The success criterion is "two people can collaborate on resume scoring", nothing more.
- **Data export burden.** GDPR export needs to include resumes, matches, agent runs, billing history. Plan the schema; don't bolt on later.
- **Resume version explosion.** Garbage-collect orphan versions. Cap free-tier users at 5 versions; paid at 50.

## Folder additions

```
apps/api/src/matchlayer_api/
  api/billing/                      # Stripe webhook + subscription endpoints
  api/admin/                        # admin endpoints (already partial from Phase 5)
  api/teams/
  api/analytics/
  services/permissions.py           # central tier check
  services/billing.py
  services/teams.py
apps/web/src/app/(app)/
  pricing/
  dashboard/
  resumes/[id]/versions/
apps/web/src/app/(admin)/
  users/
  system/
docs/adr/0007-saas-and-billing.md
docs/runbooks/stripe-incident.md
```

DB additions:

- `subscriptions` (id, user_id, stripe_customer_id, stripe_subscription_id, plan, status, current_period_end, cancel_at_period_end, created_at, updated_at)
- `stripe_events` (id, stripe_event_id unique, type, payload_json, processed_at)
- `resume_versions` modeled by adding `parent_resume_id` and `version_number` to `resumes`, or a new `resume_versions` join table — pick one in design phase.
- `teams` (id, name, owner_user_id, plan, created_at)
- `team_members` (id, team_id, user_id, role, created_at)
- `mfa_secrets` (user_id, totp_secret_encrypted, enrolled_at, recovery_codes_hashed_json)
- Existing `audit_log` (from Phase 1) extended with billing + team + MFA events.

## Work breakdown

1. Wire Stripe Checkout → backend webhook handler → `subscriptions` table.
2. Add `Permissions` service; refactor existing endpoints to use it.
3. Build pricing page; integrate with Checkout.
4. Add subscription state to user object; gate features by tier.
5. Implement resume versioning: schema + endpoints + diff view.
6. Build "Apply suggestions" → creates new version flow.
7. Personal analytics: query layer + dashboard UI.
8. Admin dashboard: extend Phase 5's admin area with users/plans/audit.
9. Team mode: workspaces, invites, role check, shared library scoping.
10. Implement GDPR data export + account deletion job.
11. Audit-log middleware for all admin and billing actions.
12. Stripe failure runbook.
13. Write the ADR.

## Definition of done

A new visitor can land on the marketing page, choose a plan, pay through Stripe, use Pro features immediately, manage their subscription, version their resume, and (if on Team) invite a colleague — with admin oversight and audit logging behind the scenes.
