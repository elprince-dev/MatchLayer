# Timing-equalization tests

## Introduction

A timing-equalization test is an automated test that measures how long two
different code paths take to respond and asserts that the difference between them
is small enough that an outside observer cannot tell the two paths apart by a
stopwatch alone. This document is about one specific use of that technique:
proving that a failed login takes the same amount of time whether the email
belongs to a real account or to no account at all. That equal-time guarantee is
the second half of the defence against account enumeration — an attacker's
ability to discover which email addresses have accounts on a system by watching
how the system answers — and a return message that reads the same for both
failures is only worth anything if the response _time_ reads the same too.

The reason a naive login leaks timing in the first place is a slow password
check. The password verifier here is built on Argon2id, a deliberately
memory-hard and time-costly function for turning a password into a stored
verifier; running it takes a fixed, noticeable amount of
central processing unit (CPU) time. When an account exists, every wrong-password
attempt pays that cost; when the account does not exist, a careless
implementation skips the check and answers sooner. A timing-equalization test is
the instrument that catches that
gap before it ships.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a timing side channel is and why a test, not a manual check, is the right tool to guard against one.
- Describe how a timing-equalization test samples many trials and compares medians rather than single measurements.
- Explain why such a test is run locally and excluded from shared continuous-integration runners.
- Recognise the common ways a timing test is written so that it passes without actually proving anything.

Prerequisites:

- [No account enumeration](06-auth-10-no-account-enumeration.md) — explains the response-time equalization defence that this test verifies; read it first so the behaviour under test makes sense.
- [Password hashing with Argon2id](06-auth-02-password-hashing-argon2id.md) — explains the slow, memory-hard hash whose cost is the source of the timing gap this test measures.

## Problem it solves

The behaviour being protected is invisible to ordinary tests. A normal functional
test asserts on _what_ a response contains: its status code, its body, its
headers. Two failed logins can return byte-for-byte identical responses and still
betray which email is real, because one of them took longer to produce. The leak
lives in the _duration_, a dimension that the usual assertions never look at. So
the question is: how do you write a regression test for a property that is not in
the response at all, but in the clock?

Before a dedicated timing test existed, the only guard against a timing
regression was discipline and code review. A reviewer had to notice, by reading
the login code, that both the unknown-account branch and the wrong-password
branch run the same expensive hash verification, and that neither one returns
early. That is fragile: a well-meaning refactor that adds an early `return` for
unknown accounts — to "save work" — silently reopens the side channel, and no
functional test goes red, because every response body still matches. Human review
is not a repeatable, automated safety net, and a side channel that only a careful
reader can spot is one that will eventually slip through.

A timing-equalization test converts that invisible property into a measurable,
executable assertion. It drives the two failure paths many times, records how
long each takes, and fails the build if the typical durations drift apart by more
than a small budget. The property that used to depend on a reviewer's attention
now has a test that re-checks it every time it is run.

## Mental model

Picture a bank with a rule that every rejection must look and feel identical, so a
fraudster in line cannot tell a real account from a fake one. The bank already
trained its tellers to say the same word and take the same ten seconds for every
"declined". The timing-equalization test is the **mystery shopper the bank hires
to audit that training**: someone who stands in line with a stopwatch, submits a
batch of known-fake names and a batch of known-real names, times every rejection,
and reports back whether the two batches felt the same. One slow teller, or one
shortcut on the fake-name line, and the audit fails.

A single visit proves nothing — one transaction might be slow because the teller
sneezed. The mystery shopper's method is what makes the audit trustworthy, and it
follows the same shape every timing-equalization test does:

1. **Set up a known-real subject.** Create one account with a real stored password so the "account exists, wrong password" path has something to run against.
2. **Warm up.** Run a handful of throwaway requests first, because the very first request pays one-time start-up costs (loading code, opening a database connection) that would distort the measurement.
3. **Sample each path many times.** Send many login attempts for unknown emails and many wrong-password attempts for the known email, timing each one with a high-resolution clock and storing the durations.
4. **Compare the typical case, not the worst case.** Take the median of each batch — the middle value, which a few unusually slow outliers cannot drag around — and compute the gap between the two medians.
5. **Assert against a budget.** Fail if the gap exceeds a small threshold; pass if the two paths are indistinguishable within that budget.

## How it works

The thing being measured is a side channel: information that leaks not through the
intended output of an operation but through a physical or observable property of
running it, such as how long it takes. A timing side channel exists whenever two
otherwise-identical responses take measurably different amounts of time, because
the duration itself carries a bit of secret information — here, whether an account
exists.

A test for a timing side channel cannot trust a single measurement. Real systems
are noisy: garbage-collection pauses, operating-system scheduling, network jitter,
and competing work on the machine all add random delay to any one request. So the
test takes many samples of each path — typically a hundred or more — to average
out that noise. The first few requests are discarded as a warm-up, because the
initial request to a freshly started service pays one-time costs (compiling code
on first use, establishing a database connection, filling caches) that are not
representative of steady-state behaviour.

From each batch of samples the test computes a summary statistic and compares the
two summaries. The median — the middle value when the samples are sorted — is the
usual choice rather than the mean (the arithmetic average), because the median is
robust to outliers: a handful of requests that happened to be very slow pull the
mean upward but barely move the median. The test then asserts that the absolute
difference between the two medians is at most a small budget, often a few tens of
milliseconds. A difference inside the budget means the two paths are
indistinguishable to an attacker timing them; a difference outside it means a
real, exploitable gap has appeared.

Two design choices keep such a test honest:

- **A high-resolution monotonic clock.** The timer used must measure short
  intervals precisely and must never run backwards (a clock that can be adjusted
  by the operating system mid-measurement would produce nonsense deltas). A
  dedicated performance counter, rather than the wall-clock time-of-day, is the
  right tool.
- **Resetting shared state between trials.** If the path under test has a side
  effect — for example, locking an account after several failed attempts — the
  test must undo that effect between samples, or later trials measure a different
  code path (a fast "account locked" rejection) than the one it intends to time.

There is a deliberate limit to what this kind of test can promise. Timing
equalization is best-effort, not perfect: enough averaging by a determined
attacker can sometimes still tease out a residual sub-millisecond difference. The
test is calibrated to catch the large, reliable gap — the presence or absence of
an entire slow hash verification — which is the gap that makes enumeration cheap
and practical. It is not trying to defeat a laboratory-grade timing attack.

That sensitivity is also why a timing test is treated specially in automation. On
a shared build runner many jobs compete for the same processor, so background
contention regularly adds tens of milliseconds of noise — enough to make a strict
sub-budget assertion fail at random even when the code is correct. A timing test
that flaps red for environmental reasons trains people to ignore it, which is
worse than not having it. The standard answer is to tag the test as
timing-sensitive, exclude it from the noisy automated pipeline by default, and run
it on a quiet local machine where the measurement means something.

## MatchLayer Phase 1 usage

The timing-equalization test for the login path lives at
`apps/api/tests/timing/test_login_timing_local.py`. It exercises the exact
behaviour described in [No account enumeration](06-auth-10-no-account-enumeration.md): the
unknown-account branch verifies a decoy hash so it pays the same cost as the
wrong-password branch.

The test is tagged so it is excluded from the shared pipeline and skipped when no
local database is reachable. The `timing` marker is what the
continuous-integration (CI) job filters out:

Source: `apps/api/tests/timing/test_login_timing_local.py`

```python
pytestmark = [
    pytest.mark.timing,
    pytest.mark.skipif(
        not _postgres_available(),
        reason="Postgres not available for timing test",
    ),
]
```

That marker is declared once in the backend's test configuration at
`apps/api/pyproject.toml`, and the default test invocation deselects it with
`-m "not timing"`:

Source: `apps/api/pyproject.toml`

```text
markers = [
    "timing: timing-sensitive tests (e.g., login-timing INV-5); excluded from CI via -m 'not timing'",
]
```

The sample size and the failure budget are fixed as module-level constants. The
budget is the maximum tolerated gap between the two medians:

Source: `apps/api/tests/timing/test_login_timing_local.py`

```python
SAMPLE_COUNT = 100
MAX_MEDIAN_DELTA_MS = 25  # Requirement 2.4
```

Each path is sampled `SAMPLE_COUNT` times with a high-resolution performance
counter wrapped around the login request. The unknown-email batch uses a fresh
random address every iteration so no account ever matches:

Source: `apps/api/tests/timing/test_login_timing_local.py`

```python
            # Unknown email trials.
            for _ in range(SAMPLE_COUNT):
                start = time.perf_counter()
                await client.post(
                    "/api/v1/auth/login",
                    json={
                        "email": f"unknown-{uuid.uuid4()}@example.com",
                        "password": "AttemptPassword12345",
                    },
                )
                unknown_times.append(time.perf_counter() - start)
```

Finally the test reduces each batch to its median, takes the absolute difference,
and asserts it stays within the budget — the single line that encodes the whole
security property:

Source: `apps/api/tests/timing/test_login_timing_local.py`

```python
    median_unknown_ms = statistics.median(unknown_times) * 1000
    median_wrong_ms = statistics.median(wrong_times) * 1000
    delta_ms = abs(median_unknown_ms - median_wrong_ms)

    assert delta_ms <= MAX_MEDIAN_DELTA_MS, (
        f"Login timing delta {delta_ms:.1f} ms exceeds the {MAX_MEDIAN_DELTA_MS} ms "
        f"budget (Requirement 2.4). Unknown median: {median_unknown_ms:.1f} ms, "
        f"wrong-password median: {median_wrong_ms:.1f} ms."
    )
```

Because the wrong-password batch resets the known user's failed-login counter
between iterations, every trial in that batch measures the same wrong-password
branch rather than a later account-lockout rejection — the shared-state reset the
conceptual section warns about, applied in practice.

## Common pitfalls

- **Mistake:** Timing each path once instead of sampling many trials and comparing a summary statistic.
  **Symptom:** The test passes or fails at random between runs with no code change, because a single measurement is dominated by whatever noise happened during that one request.
  **Recovery:** Take many samples per path (a hundred or more), discard a few warm-up requests, and assert on the median of each batch rather than on any individual timing.

- **Mistake:** Comparing the mean of each batch rather than the median.
  **Symptom:** A few unusually slow requests — a garbage-collection pause, a scheduler hiccup — inflate one batch's mean and trip the assertion even though the typical request times are equal.
  **Recovery:** Summarise each batch with the median, which a small number of outliers cannot drag around, and compare the two medians.

- **Mistake:** Running the timing test in the shared continuous-integration pipeline alongside every other job.
  **Symptom:** The test flaps red intermittently on the build server because background jobs contend for the processor and add tens of milliseconds of noise, and the team learns to ignore its failures.
  **Recovery:** Tag the test as timing-sensitive, exclude it from the default automated run, and execute it on a quiet local machine where the measurement is meaningful.

- **Mistake:** Forgetting to reset state that the path under test mutates, such as a failed-login counter that triggers account lockout.
  **Symptom:** Later iterations of the wrong-password batch are suddenly much faster because they hit a short-circuit "account locked" rejection, so the median reflects a path the test never meant to measure.
  **Recovery:** Undo the side effect between trials — reset the counter and any lock — so every sample exercises the same branch from the same starting state.

## External reading

- [pytest — working with custom markers](https://docs.pytest.org/en/stable/how-to/mark.html)
- [Python documentation — `time.perf_counter` and monotonic clocks](https://docs.python.org/3/library/time.html)
- [Python documentation — the `statistics` module (`median`)](https://docs.python.org/3/library/statistics.html)
- [Open Worldwide Application Security Project (OWASP) — Testing for Account Enumeration and Guessable User Account](https://owasp.org/www-project-web-security-testing-guide/latest/4-Web_Application_Security_Testing/03-Identity_Management_Testing/04-Testing_for_Account_Enumeration_and_Guessable_User_Account)
- [Request for Comments (RFC) 9106 — Argon2 Memory-Hard Function for Password Hashing](https://datatracker.ietf.org/doc/html/rfc9106)
