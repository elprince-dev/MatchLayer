# Rate limiting and account lockout

## Introduction

This document explains two defences that sit in front of authentication
endpoints: rate limiting and account lockout. Rate limiting is the practice of
capping how many requests a single caller may make in a rolling stretch of time,
so that automated abuse is throttled before it does damage. Account lockout is a
narrower control that temporarily refuses logins to one specific account after a
run of failed password attempts, so that guessing a single victim's password
becomes impractical. Both lean on a shared, fast counter that every server
process can see — here that counter lives in Redis, an in-memory key-value store
that keeps its data in main memory for speed. The two controls answer different
threats: rate limiting blunts broad flooding and credential-stuffing across many
accounts, while lockout protects one account under a focused guessing attack.

**Learning outcomes** — after reading this document you will be able to:

- Explain the difference between a fixed-window and a sliding-window rate limit, and why the sliding window avoids the burst problem.
- Describe how a shared in-memory store lets many server processes enforce one global limit atomically.
- Explain how an account-lockout policy counts failed logins within a rolling window and when it trips.
- Recognise the common configuration and design mistakes in both controls and recover from them.

Prerequisites:

- [Redis fundamentals and the Phase 1 standby](07-database-03-redis-fundamentals.md) — introduces the in-memory key-value store that both controls count against.

## Problem it solves

An authentication endpoint is an open door to the internet: anyone can send it
requests, and each request costs the server work (a password hash verification,
a database lookup, a token mint). Two abuses follow directly. The first is
volume — a script firing thousands of login attempts a minute, either to guess
passwords (credential stuffing, replaying leaked username-and-password pairs) or
to exhaust the server. The second is targeted guessing — many attempts against
one account's password until one lands.

A naive prior approach is to count attempts per caller in each server process's
own memory. That breaks the moment there is more than one process: a counter in
one process is invisible to the others, so an attacker spreading requests across
processes evades every local limit, and each restart wipes the count. Another
naive approach is the fixed window — count attempts per clock minute and reset to
zero at the top of each minute. The fixed window has a notorious flaw: an
attacker can send a full allowance in the last second of one minute and another
full allowance in the first second of the next, doubling the intended rate across
the boundary.

A separate problem is that volume limits alone do not stop a patient attacker who
stays under the rate cap but keeps guessing one account forever. That is the gap
account lockout fills: it watches a single account's failures and locks it for a
cooling-off period once they cross a threshold, independent of the global rate.

## Mental model

Think of a club doorkeeper holding a stopwatch and a notepad. Each time someone
from a given address knocks, the doorkeeper writes the exact timestamp on the
notepad under that address's name.

1. When a new knock arrives, the doorkeeper first crosses out every timestamp older than the window — say, older than fifteen minutes ago — so the notepad only ever holds recent knocks.
2. The doorkeeper counts the timestamps that remain for that address.
3. If the count is already at the limit, the knock is refused, and the doorkeeper can even say how many seconds until the oldest timestamp ages out and a slot frees up.
4. If the count is under the limit, the doorkeeper records this knock's timestamp and lets the request through.

Because the doorkeeper always measures the last fifteen minutes ending _now_ —
not a fixed quarter-hour on the wall clock — there is no boundary to exploit. The
window slides forward with every knock. This is a sliding window, in contrast to
a fixed window that resets on the clock.

Account lockout is a second notepad the same doorkeeper keeps, but per account
rather than per address: a tally of recent failed password attempts for one
member. When that tally reaches the threshold, the member's account is marked
"locked until" a near-future time, and knocks for that account are turned away
until the clock passes that time — even if the password offered is finally
correct.

## How it works

A rate limiter needs a counter that is shared across every server process and
that can be read and updated atomically — in one indivisible step, so two
simultaneous requests cannot both read a stale count and both decide they are
under the limit. An in-memory store reachable over the network provides exactly
that: one source of truth, fast enough to consult on every request.

The sliding-window algorithm models each caller's recent activity as a set of
timestamped entries under one key. On each request the limiter does four things
in a single atomic operation: remove every entry older than the window, count the
entries that remain, reject if the count is at or above the limit, and otherwise
add an entry stamped with the current time. A sorted set — a collection whose
members are ordered by a numeric score — is the natural structure, using the
timestamp as the score so the "remove everything older than now minus the window"
step is a single range deletion. When the limiter rejects a request, it can
compute how long the caller must wait by looking at the oldest surviving entry:
once that entry ages past the window, a slot opens. That wait is returned to the
client as a hint, conventionally in a `Retry-After` response header.

Running the four steps as one server-side script matters. If they ran as separate
round trips, two requests could interleave between the count and the add and both
slip through. Pushing the whole check-and-record into a single atomic script
executed by the store closes that race.

A critical design choice is what to do when the shared store is unreachable. The
safe default for a security control is to fail closed — deny the request rather
than wave it through — because an attacker who can knock the counter offline
should not thereby gain unlimited attempts. The cost is that a store outage also
blocks legitimate users, so fail-closed behaviour is a deliberate
availability-versus-security trade made consciously for authentication paths.

Account lockout is a different shape of counter. Rather than a set of timestamps,
it keeps two facts per account: a running count of consecutive recent failures
and the time of the most recent failure. On each failed login the policy asks
whether the previous failure fell inside the window. If it did, the count
increments; if it did not, this failure starts a fresh window with a count of
one. That reset prevents slow, sporadic failures over days from silently
accumulating to the threshold and locking an account that was never under attack.
When the count reaches the threshold, the account is stamped with a "locked until"
time a fixed duration in the future, and the running count is reset so that a new
burst after the lock expires starts from a clean slate. While locked, login
attempts are refused up front without even checking the password.

One subtlety ties lockout to a related defence called account enumeration — the
ability of an attacker to learn whether an account exists by watching how the
system responds. A lockout response, an error message, or a noticeably different
response time can all leak that signal, so a careful design keeps the locked
response and timing indistinguishable from an ordinary failed login wherever it
can.

## MatchLayer Phase 1 usage

The sliding-window limiter lives in `apps/api/src/matchlayer_api/core/rate_limit.py`,
which is the only module in the backend permitted to import the Redis client.
The four-step check runs as a single Lua script executed inside Redis, so the
remove-count-reject-add sequence is atomic:

Source: `apps/api/src/matchlayer_api/core/rate_limit.py`

```python
_LUA_SCRIPT = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)
local count = redis.call('ZCARD', key)
if count >= limit then
  local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
  local retry_after_ms = (tonumber(oldest[2]) + window_ms) - now_ms
  if retry_after_ms < 0 then retry_after_ms = 0 end
  return {0, math.ceil(retry_after_ms / 1000)}
end
redis.call('ZADD', key, now_ms, member)
redis.call('PEXPIRE', key, window_ms)
return {1, 0}
"""
```

The Python wrapper records each request as a uniquely-named member scored by the
current millisecond timestamp, and converts any Redis error into a fail-closed
decision rather than letting the request through:

Source: `apps/api/src/matchlayer_api/core/rate_limit.py`

```python
    async def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitDecision:
        """Check and record a request against the sliding window.

        On any Redis error, returns fail-closed (§10.4).
        """
        now_ms = int(time.time() * 1000)
        window_ms = window_seconds * 1000
        member = f"{now_ms}:{secrets.token_hex(4)}"

        try:
            if self._script is None:
                self._script = self._redis.register_script(_LUA_SCRIPT)
            result = await self._script(
                keys=[key],
                args=[now_ms, window_ms, limit, member],
            )
            allowed = bool(result[0])
            retry_after = int(result[1])
            return RateLimitDecision(allowed=allowed, retry_after_seconds=retry_after)
        except Exception:
            _log.warning("rate_limiter_redis_error", key=key)
            return RateLimitDecision(
                allowed=False,
                retry_after_seconds=60,
                redis_unavailable=True,
            )
```

The per-endpoint limits and windows are typed settings in
`apps/api/src/matchlayer_api/config.py`. Login is limited both per email and per
Internet Protocol (IP) address so that one attacker cannot grind a single account
and a shared address cannot fan out across many accounts unchecked:

Source: `apps/api/src/matchlayer_api/config.py`

```python
    auth_rate_limit_login_email_limit: int = 10
    auth_rate_limit_login_email_window_seconds: int = 900
    auth_rate_limit_login_ip_limit: int = 50
    auth_rate_limit_login_ip_window_seconds: int = 900
```

Account lockout is implemented separately in
`apps/api/src/matchlayer_api/services/auth.py`. The policy evaluates the rolling
window using a per-user `last_failed_login_at` column rather than an auxiliary
table, resets the count when the previous failure fell outside the window, and on
threshold-reach stamps `locked_until` and emits an `account_locked` audit-log
entry — an append-only security event record:

Source: `apps/api/src/matchlayer_api/services/auth.py`

```python
        within_window = user.last_failed_login_at is not None and (
            now - user.last_failed_login_at
        ) <= timedelta(seconds=window_seconds)
        new_count = user.failed_login_count + 1 if within_window else 1

        if new_count >= threshold:
```

The thresholds themselves are settings, defaulting to ten failures inside a
fifteen-minute window and a fifteen-minute lock:

Source: `apps/api/src/matchlayer_api/config.py`

```python
    auth_lockout_threshold: int = 10
    auth_lockout_window_seconds: int = 900
    auth_lockout_duration_seconds: int = 900
```

The limiter is wired into routes through a FastAPI dependency in
`apps/api/src/matchlayer_api/core/dependencies.py` that reads the per-endpoint
policy, calls the limiter for each key category (for login, both email and IP),
sets `Retry-After` on rejection, and raises a 503 when Redis is unreachable so the
fail-closed decision surfaces as a service-unavailable response rather than a
silent allow.

## Common pitfalls

- **Mistake:** Using a fixed-window counter that resets on the wall clock instead of a sliding window.
  **Symptom:** An attacker sustains close to double the intended rate by bunching requests around the window boundary — a burst at the end of one window and another at the start of the next — yet the per-window count never appears to exceed the limit.
  **Recovery:** Switch to a sliding window that measures the last N seconds ending at the current request, so there is no fixed boundary to straddle; the sorted-set approach above does this in one atomic step.

- **Mistake:** Running the count and the record as two separate store operations instead of one atomic script.
  **Symptom:** Under concurrency the limit is overshot — two simultaneous requests both read a count below the limit and both proceed, so more requests slip through than the limit should allow.
  **Recovery:** Push the entire remove-count-reject-add sequence into a single server-side script (or an equivalent atomic primitive) so no other request can interleave between the check and the record.

- **Mistake:** Failing open when the shared counter store is unreachable, so requests are allowed through whenever the store errors.
  **Symptom:** An attacker who can disrupt or overload the counter store gains unlimited unthrottled attempts, and the outage that should have tightened security instead removes it.
  **Recovery:** Fail closed on store errors for authentication paths — deny and return a retry hint — and monitor the store's availability so the outage is fixed quickly rather than masked.

- **Mistake:** Never resetting the failed-login counter, so sporadic failures accumulate across days toward the lockout threshold.
  **Symptom:** A legitimate user who mistypes a password occasionally over a week is locked out despite never being under attack, because old, unrelated failures were still counted.
  **Recovery:** Reset the counter to one whenever the previous failure fell outside the rolling window, so only a genuine burst of recent failures can trip the lock.

## External reading

- [Redis: sorted sets](https://redis.io/docs/latest/develop/data-types/sorted-sets/)
- [Redis: ZREMRANGEBYSCORE](https://redis.io/docs/latest/commands/zremrangebyscore/)
- [MDN Web Docs: Retry-After header](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Retry-After)
- [MDN Web Docs: 429 Too Many Requests](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Status/429)
- [OWASP: Blocking Brute Force Attacks](https://owasp.org/www-community/controls/Blocking_Brute_Force_Attacks)
