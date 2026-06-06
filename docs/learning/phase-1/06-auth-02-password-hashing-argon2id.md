# Password hashing with Argon2id and a common-password blocklist

## Introduction

A password must never be stored the way a user typed it. If the database that
holds accounts is ever stolen, every plaintext password in it is stolen too, and
because people reuse passwords, that one breach cascades into other services. The
defence is to store a one-way transformation of the password — a value computed
from it that cannot be reversed back into the original — and to recompute that
value at login time to check the two match. This document explains how a modern
password-hashing function called Argon2id (a deliberately slow, memory-hungry
algorithm purpose-built for storing passwords) produces that value, why "slow"
is the point, and how a separate list of the most common passwords closes a gap
that hashing alone cannot.

The document also covers two policy decisions that sit alongside the hash: a
minimum-length rule that is counted carefully so a clever single character cannot
sneak under it, and the practice of refusing passwords that appear on a published
list of the thousand most-guessed credentials.

**Learning outcomes** — after reading this document you will be able to:

- Explain why passwords are stored as a slow one-way hash rather than encrypted or in plaintext.
- Describe what makes Argon2id "memory-hard" and why that resists offline cracking on specialized hardware.
- Explain how Unicode normalization keeps the same logical password from hashing two different ways.
- Describe why a common-password blocklist is enforced separately from the length rule, and how a sorted list makes that check fast.
- Recognise the common ways password storage is implemented insecurely and how to correct each one.

Prerequisites:

- [Threat-model categories](05-security-06-threat-model-categories.md) — names the account-takeover and credential-stuffing threats that password hashing and the blocklist defend against.
- [No account enumeration](06-auth-10-no-account-enumeration.md) — covers the login-timing defence that reuses the same hashing primitive this document introduces.

## Problem it solves

The concrete problem is database theft. Sooner or later some account store leaks —
through a misconfigured backup, a Structured Query Language (SQL) injection bug,
or a stolen disk image. The
question is what an attacker holding that dump can do with it. If the passwords
were stored in plaintext, the answer is "everything, immediately". If they were
stored with a fast hash, the answer is "almost everything, within hours".

The earliest approach was to store passwords exactly as typed. That fails the
moment the store leaks. The next approach was to run each password through a fast
cryptographic digest — a general-purpose hash function designed to be quick, such
as the older Message-Digest 5 or the Secure Hash Algorithm (SHA) family — and
store the digest. This is better — the digest cannot be
trivially read back — but "fast" is precisely the weakness. An attacker with the
dump runs the same fast digest over billions of candidate passwords per second on
a graphics processing unit (GPU), comparing each result to the stolen digests.
Common passwords fall in seconds. Adding a per-password random value called a
**salt** (stored next to the hash) stops one precomputed table from cracking
every account at once, but it does not slow down guessing a single account.

The remaining gap is human behaviour. Even a slow, salted hash cannot save an
account whose password is `password123` or `qwerty`, because those are the very
first guesses any attacker tries. Slowing the hash raises the cost per guess;
refusing common passwords removes the guesses that are nearly free. The two
defences are complementary, and a careful system applies both.

## Mental model

Think of storing a password like a bank verifying a signature, but with a twist:
the bank is forbidden from keeping a copy of your actual signature. Instead, when
you open the account, the teller watches you sign and records a long, careful
description of _how_ you sign — pressure, slant, timing — in a way that cannot be
turned back into your signature. At each visit you sign again, the teller derives
the same description, and the two descriptions are compared. A thief who steals
the filing cabinet gets the descriptions, not the signatures, and reproducing a
signature from its description is the hard, slow work the scheme is designed to
impose.

A password hash works the same way, in four steps:

1. **Normalize the input.** Collapse the typed password into one canonical byte sequence so the same logical password always looks identical (more on this below).
2. **Add salt and hash slowly.** Combine the password with a fresh random salt and run a deliberately expensive function over the result, producing a fixed-size output that cannot be reversed.
3. **Store the recipe with the result.** Save the algorithm name, its cost settings, the salt, and the output together as one self-describing string, so a future check knows exactly how to reproduce it.
4. **Verify by recomputing.** At login, run the stored recipe over the submitted password and compare; if the settings have since been strengthened, transparently recompute and save a stronger value.

The "slow" in step 2 is a feature, not a bug. A legitimate login pays the cost
once and a human never notices a fraction of a second. An attacker guessing
billions of times pays it billions of times, which is what makes a stolen dump
expensive rather than instant to crack.

## How it works

Argon2id is a **memory-hard** password-hashing function — meaning each evaluation
is engineered to require a large, fixed block of memory in addition to processor
time, so the work cannot be made cheap by throwing specialized hardware at it. It
was selected as the winner of a public Password Hashing Competition (PHC) and is
standardized as Request for Comments (RFC) 9106. The "id" suffix marks the hybrid
variant the Open Worldwide Application Security Project (OWASP) recommends for
general password storage, because it resists both side-channel observation and
trade-off attacks that swap memory for time.

Why does memory-hardness matter? A fast digest can be cracked on a GPU because a
GPU has thousands of small cores, each able to compute a cheap hash in parallel.
Those cores share a relatively small pool of memory. By forcing every single
evaluation to occupy a large memory block for its whole duration, a memory-hard
function starves those parallel cores — the attacker can no longer run thousands
of guesses at once because there is not enough memory to go around. The same
property defeats custom cracking chips, whose advantage is cheap arithmetic, not
cheap memory.

Argon2id is tuned by three numbers, and choosing them is the heart of using it
well:

- **Time cost** — how many passes the algorithm makes over its memory. More passes, more central processing unit (CPU) time per guess.
- **Memory cost** — how much memory each evaluation must occupy, the dominant lever for resisting parallel cracking.
- **Parallelism** — how many lanes the work is split into.

These parameters are not secret. They are stored, in the clear, as part of the
output, because verification must know them to reproduce the hash. The conventional
storage format is a single self-describing string — often called a Password
Hashing Competition (PHC) string — that packs the algorithm identifier, the
parameter values, the salt, and the derived output into one field. Storing the
parameters with the hash is what makes it possible to raise the cost over time:
old hashes carry their old settings, new logins can be re-hashed at the stronger
settings, and the two coexist in the same column.

One subtlety trips up Unicode handling. The same logical password can be typed as
different byte sequences — an accented character such as `é` can arrive as one
code point or as a plain letter followed by a combining accent mark. If the raw
bytes were hashed directly, a user who registered one way and logged in the other
would be locked out. The fix is **normalization**: before hashing, the password
is run through a canonical form known as Normalization Form Compatibility
Composition (NFKC) that rewrites equivalent sequences into one agreed
representation, so the two inputs collapse to the same bytes.

A length floor adds one more wrinkle. A minimum-length policy should count the
characters the user actually typed, _before_ normalization, because NFKC can
expand a single compatibility character into several. Counting after normalization
would let one cleverly chosen glyph satisfy a twelve-character minimum, so the
count is taken on the raw input.

Finally, hashing cannot rescue a guessable password. A separate control compares
the candidate against a list of the most common passwords and rejects any match.
To keep that check cheap, the list is stored sorted, so membership is found by
binary search — repeatedly halving the search range — in a number of steps
proportional to the logarithm of the list size, rather than scanning every entry.

## MatchLayer Phase 1 usage

The password-hashing helper lives at
`apps/api/src/matchlayer_api/core/security/passwords.py`, and it is deliberately
the only module in the backend that imports the `argon2-cffi` library. Keeping the
dependency behind one module means the Argon2id parameters and the blocklist logic
have a single, auditable home.

The hasher is constructed once at import with the parameters pinned explicitly
rather than left to the library defaults, so an upstream change cannot silently
shift the cost:

Source: `apps/api/src/matchlayer_api/core/security/passwords.py`

```python
_hasher = _Argon2Hasher(
    time_cost=1,
    memory_cost=65536,
    parallelism=1,
    hash_len=32,
    salt_len=16,
)
```

The `memory_cost=65536` value is measured in kibibytes, so every evaluation
occupies 64 mebibytes of memory — the memory-hardness lever from the previous
section, expressed as a concrete number.

Hashing applies the length floor first, then normalizes, then hashes. The floor is
checked against the raw plaintext so a compatibility glyph cannot expand its way
past the minimum:

Source: `apps/api/src/matchlayer_api/core/security/passwords.py`

```python
    if len(plaintext) < MIN_PASSWORD_LENGTH:
        raise PasswordTooShortError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")
    return _hasher.hash(_normalize(plaintext))
```

Verification recomputes the hash and additionally reports whether the stored value
was produced under weaker settings than the current policy, so the caller can
transparently upgrade it on a successful login:

Source: `apps/api/src/matchlayer_api/core/security/passwords.py`

```python
    needs_rehash = _hasher.check_needs_rehash(stored)
    return True, needs_rehash
```

The common-password blocklist is a separate control. Its data lives next to the
helper at `apps/api/src/matchlayer_api/core/security/password_blocklist.txt` — a
plain-text file of roughly a thousand of the most common passwords, already
lower-cased, NFKC-normalized, deduplicated, and sorted lexicographically so the
code can search it without re-sorting. The file is read once at import and the
comment and blank lines are filtered out:

Source: `apps/api/src/matchlayer_api/core/security/passwords.py`

```python
_BLOCKLIST_PATH = Path(__file__).parent / "password_blocklist.txt"
_BLOCKLIST: list[str] = [
    line
    for line in _BLOCKLIST_PATH.read_text(encoding="utf-8").splitlines()
    if line and not line.startswith("#")
]
```

Membership is then a binary search over that sorted list rather than a linear
scan, after normalizing and lower-casing the candidate to match the on-disk form:

Source: `apps/api/src/matchlayer_api/core/security/passwords.py`

```python
def is_blocked(plaintext: str) -> bool:
    normalized = _normalize(plaintext).lower()
    idx = bisect.bisect_left(_BLOCKLIST, normalized)
    return idx < len(_BLOCKLIST) and _BLOCKLIST[idx] == normalized
```

A throwaway example makes the contract concrete: a registration request carrying
`"password1234"` is long enough to clear the length floor but, being a textbook
common credential, is rejected by `is_blocked` before any account is created. The
two checks are kept separate so the error returned to the caller can say _which_
rule failed — too short versus too common — and so the cheap blocklist check can
run before paying the Argon2id cost on a request that will be refused anyway.

## Common pitfalls

- **Mistake:** Storing passwords with a fast general-purpose digest such as MD5, SHA-1, or SHA-256 (optionally salted) instead of a slow password-hashing function.
  **Symptom:** Each stored value is short and uniform, computes in microseconds, and a leaked dump is cracked at billions of guesses per second on commodity hardware; security review flags the digest call.
  **Recovery:** Switch to Argon2id (or another memory-hard function), store the full self-describing hash string, and re-hash each password on the user's next successful login.

- **Mistake:** Hashing the raw typed bytes without Unicode normalization.
  **Symptom:** Some users — typically those using accented characters or non-Latin input methods — can register but then cannot log in with what they believe is the same password; verification fails intermittently and irreproducibly.
  **Recovery:** Normalize the password to a canonical form such as NFKC before both hashing and verifying, so equivalent inputs collapse to identical bytes.

- **Mistake:** Counting the minimum length _after_ normalization, or skipping the blocklist because "the length rule is enough".
  **Symptom:** A single compatibility character slips under the length floor after expanding, or a short common password such as a dictionary word is accepted; weak-credential accounts appear in the data.
  **Recovery:** Count length on the pre-normalization input, and enforce the common-password blocklist as a distinct check that rejects known-guessable values regardless of length.

- **Mistake:** Implementing the blocklist as a linear scan, or loading and parsing the list file on every request.
  **Symptom:** Registration and password-change latency grows with the size of the list, and the file is read from disk on every call; profiling shows time spent re-parsing the same data.
  **Recovery:** Load the sorted list once at import and look candidates up with a binary search so each check is logarithmic in the list size and touches the disk only once.

## External reading

- [OWASP Password Storage Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html)
- [Argon2 Memory-Hard Function for Password Hashing (RFC 9106)](https://datatracker.ietf.org/doc/html/rfc9106)
- [argon2-cffi documentation](https://argon2-cffi.readthedocs.io/en/stable/)
- [Python `unicodedata` — Unicode normalization](https://docs.python.org/3/library/unicodedata.html)
- [Python `bisect` — array bisection algorithm](https://docs.python.org/3/library/bisect.html)
