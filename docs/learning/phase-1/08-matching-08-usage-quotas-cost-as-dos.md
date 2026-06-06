# Usage quotas as a cost-as-DoS defense

## Introduction

This document explains per-user daily usage quotas: a hard ceiling on how many
times one account may perform an expensive operation within a single calendar
day. A quota is different from a rate limit — a rate limit (a cap on requests
per short rolling window, covered in the prerequisite below) smooths bursts over
seconds or minutes, while a quota bounds the total work one user can cause over a
whole day no matter how slowly they pace it. The specific threat a quota answers
is cost-as-Denial-of-Service (cost-as-DoS): an attacker who cannot crash your
servers directly but can still hurt you by driving up the bill for compute,
storage, or paid third-party calls until the budget is exhausted. This document
walks through what a daily quota counts, where the day boundary falls, and how a
durable counter that survives restarts and outages enforces the ceiling.

**Learning outcomes** — after reading this document you will be able to:

- Explain how a daily usage quota differs from a per-minute rate limit and what each one defends against.
- Describe how counting committed work over a fixed calendar day produces a durable quota that does not depend on a separate cache.
- Explain why a quota counts expensive operations that already happened, including ones whose results were later deleted.
- Recognise common quota mistakes and recover from them.

Prerequisites:

- [Rate limiting and account lockout](06-auth-05-rate-limiting-and-account-lockout.md) — introduces the per-minute throttle that sits alongside the daily quota and explains the in-memory counter store the quota deliberately does not use.

## Problem it solves

Some operations are cheap and some are expensive. Serving a static page costs
almost nothing; extracting text from an uploaded file, running a scoring
computation, or calling a metered external service costs real money and real
machine time. An attacker — or a buggy client stuck in a retry loop — that
repeats an expensive operation thousands of times in a day can run up a hosting
or vendor bill large enough to take the product offline for financial reasons,
even though no single request was abusive and no server ever crashed. That is the
cost-as-Denial-of-Service (cost-as-DoS) threat: harm through accumulated expense
rather than through a flood.

A per-minute rate limit alone does not close this gap. A rate limit caps the
_speed_ of requests, so an attacker who stays under the per-minute ceiling
can still issue the maximum allowed amount every minute, all day, and the total
for the day can be enormous. The prior state, before a daily ceiling exists, is a
system where the only brake is the rate limit, and a patient caller pacing
themselves under it faces no daily bound at all. A daily quota adds the missing
ceiling: a fixed number of expensive operations per account per day, after which
further attempts are refused until the day rolls over.

## Mental model

Think of a prepaid coffee card that is topped up to a fixed number of cups every
morning at the same hour. Each cup you order punches the card once. When the
punches reach the card's limit, the barista refuses further orders and tells you
when a fresh card arrives — tomorrow morning. It does not matter whether you
drank the cups quickly or spread them across the day; what matters is the running
count since the last top-up. Spilling a cup does not earn you a refund punch: the
coffee was still poured, so it still counts.

Concretely, enforcing a daily quota on an expensive operation is a four-step
check that runs before any expensive work begins:

1. Find the start of the current day — the most recent midnight on a single agreed clock.
2. Count how many of this user's expensive operations have been recorded since that midnight.
3. If the count is already at or above the limit, refuse the new request and tell the caller when the count resets (the next midnight).
4. Otherwise, perform the operation and record it, so it counts toward today's total.

The reset is automatic and needs no scheduled job: because step 1 always recomputes
"the start of today," yesterday's records fall outside the window once the
clock passes midnight, and the count seen in step 2 drops back toward zero on its
own.

## How it works

A daily quota needs a counter that is **durable** — it must survive a process
restart or a cache outage, because a quota that resets to zero every time a
server reboots is no quota at all, and an attacker who can force a reboot could
refill their allowance at will. The most dependable place to keep that count is
the same transactional database that already records the expensive operations
themselves. Every time the operation completes, a row is written; counting those
rows _is_ counting the operations, with no separate tally to keep in sync.

The day boundary has to be unambiguous. If "today" meant the server's local
wall-clock day, the window would shift whenever a machine's timezone differed or
daylight-saving time changed, and two servers in different regions could disagree
about which day a request belongs to. Anchoring the window to Coordinated
Universal Time (UTC) — the single global reference clock that has no timezone
offset and no daylight-saving shifts — removes that ambiguity. The start of the
current day is computed as midnight UTC on today's calendar date, and an
operation counts toward today's quota when its recorded timestamp is at or after
that instant. The reset moment surfaced to a refused caller is the next midnight
UTC.

The check is a count followed by a comparison: count the user's recorded
operations since the start of the UTC day, compare against the configured limit,
and if the count meets or exceeds the limit, refuse. Refusal is expressed over
Hypertext Transfer Protocol (HTTP) as status code 429, the standard "too many
requests" response, carrying a message that names the limit and the reset time so
an honest client can back off until tomorrow. Critically, the check runs _before_
the expensive work, so a refused request performs none of the work it was trying
to abuse — the whole point is to spend nothing on a request that is over budget.

One deliberate design choice is that the count includes operations whose results
were later deleted. If a user runs an expensive operation and then removes the
result, the compute and storage were still consumed — the cost was already
incurred — so the operation still counts against the day's ceiling. Counting only
the surviving results would hand an attacker a trivial bypass: do the work,
delete it, repeat forever. A second choice is that this counter is independent of
the fast in-memory store used for per-minute rate limiting. Rate limits need
sub-millisecond reads on every request and can tolerate being approximate; the
daily quota needs durability and exactness, so it lives in the transactional
store even though that read is slightly more expensive.

## MatchLayer Phase 1 usage

MatchLayer applies two daily quotas, both as cost-as-DoS defenses: an upload
quota on resume uploads and a scoring quota on match scoring. Both are defined as
typed settings in `apps/api/src/matchlayer_api/config.py`, alongside the
per-minute rate limits so the two kinds of limit are visible together:

Source: `apps/api/src/matchlayer_api/config.py`

```python
    resume_rate_limit_per_min: int = 10
    match_rate_limit_per_min: int = 20
    resume_daily_quota: int = 20
    match_daily_quota: int = 50
```

The scoring quota is enforced in `apps/api/src/matchlayer_api/services/matching.py`,
in a helper that runs first inside `create_match`, before any resume is loaded or
any scoring is computed. It counts the user's `match_results` rows created since
the start of the Coordinated Universal Time (UTC) day, compares against the limit,
and on breach emits a `quota_rejected` audit-log entry (a row in the append-only
record of security-relevant events) naming only the quota category before raising
a 429:

Source: `apps/api/src/matchlayer_api/services/matching.py`

```python
    async def _enforce_scoring_quota(self, session: AsyncSession, *, user_id: UUID) -> None:
        now = _now()
        day_start = _start_of_utc_day(now)
        count_stmt = (
            select(func.count())
            .select_from(MatchResult)
            .where(
                MatchResult.user_id == user_id,
                MatchResult.created_at >= day_start,
            )
        )
        used = (await session.execute(count_stmt)).scalar_one()

        limit = self._settings.match_daily_quota
        if used < limit:
            return

        # Over quota: audit the rejection (ids/category only — no PII) and raise.
        await self._audit.emit(
            session,
            event_type="quota_rejected",
            user_id=user_id,
            payload={"quota": "scoring"},
        )
        reset_at = _next_utc_midnight(now)
        raise QuotaExceededError(
            f"Daily scoring quota of {limit} matches reached. "
            f"The quota resets at {reset_at.isoformat()}."
        )
```

The upload quota in `apps/api/src/matchlayer_api/services/resumes.py` follows the
same shape and is the very first step of the upload orchestration, so a
quota-rejected upload never reaches storage, never validates the file's bytes,
and never runs text extraction. The audit payload names only the quota category —
never any Personally Identifiable Information (PII) such as a filename:

Source: `apps/api/src/matchlayer_api/services/resumes.py`

```python
        now = _now()
        day_start = _utc_day_start(now)
        quota = self._settings.resume_daily_quota
        count = await session.scalar(
            select(func.count())
            .select_from(Resume)
            .where(Resume.user_id == user.id, Resume.created_at >= day_start)
        )
        uploaded_today = int(count or 0)
        if uploaded_today >= quota:
            # Stage the cost-as-DoS audit row, then refuse (Requirement
            # 11.6). The payload names only the quota category -- no PII.
            await self._audit.emit(
                session,
                event_type="quota_rejected",
                user_id=user.id,
                payload={"quota": "upload"},
            )
            reset_at = day_start + timedelta(days=1)
            raise QuotaExceededError(
                f"Daily resume upload quota of {quota} reached. "
                f"Quota resets at {reset_at.isoformat()}."
            )
```

Both quotas count by querying the transactional database directly rather than the
in-memory store used for per-minute rate limiting, so the count is exact and
durable across restarts. Both deliberately count rows that were created during the
day even if they were later soft-deleted (marked deleted without physically
removing the row), because the upload or scoring work — and its cost — already
happened.

## Common pitfalls

- **Mistake:** Storing the daily count only in the fast in-memory cache used for per-minute rate limiting, instead of in the durable transactional store.
  **Symptom:** After a process restart or a cache eviction, a user's daily count drops back toward zero mid-day, and an account that had hit its ceiling can suddenly perform many more expensive operations than the limit allows.
  **Recovery:** Count the durable records of the operations themselves (the rows already written to the database) so the count is reconstructed exactly on every check and cannot be wiped by a restart.

- **Mistake:** Computing the day boundary from the server's local wall-clock time rather than a single global clock.
  **Symptom:** The quota appears to reset at different moments on different servers, users near the boundary get inconsistent results, and a daylight-saving change shifts the window by an hour for a day.
  **Recovery:** Anchor the window to midnight in Coordinated Universal Time (UTC) on the current calendar date, so every server agrees on exactly when "today" starts and when the count resets.

- **Mistake:** Counting only the surviving results, so a deleted operation no longer counts against the quota.
  **Symptom:** A caller performs an expensive operation, deletes the result, and repeats indefinitely, driving unbounded cost while the visible count never climbs.
  **Recovery:** Count every operation recorded during the day regardless of later deletion, because the cost was incurred when the work ran, not when its output was kept.

- **Mistake:** Checking the quota after performing the expensive work instead of before it.
  **Symptom:** The bill keeps growing under abuse even though requests are returning a refusal, because each refused request still paid for the extraction, scoring, or vendor call before the limit was consulted.
  **Recovery:** Run the count-and-compare as the first step of the operation and refuse before any costly work begins, so an over-budget request spends nothing.

## External reading

- [Mozilla Developer Network (MDN) Web Docs: 429 Too Many Requests](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/429)
- [MDN Web Docs: Retry-After header](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Retry-After)
- [PostgreSQL: aggregate functions (count)](https://www.postgresql.org/docs/16/functions-aggregate.html)
- [Python documentation: datetime.combine](https://docs.python.org/3/library/datetime.html#datetime.datetime.combine)
- [Open Worldwide Application Security Project (OWASP): Denial of Service Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Denial_of_Service_Cheat_Sheet.html)
