# Structured logging as a PII defense

## Introduction

This document explains how the way a program records its activity can itself be a
privacy control, and the single piece of code that enforces that control. The
sensitive data being protected is Personally Identifiable Information (PII) — any
data that identifies a specific person, such as an email address, a phone number,
or the text of an uploaded resume. The recording technique is structured logging,
a style of logging where every log entry is a set of named fields (for example, a
field named `status` holding `200`) rather than one free-text sentence. The
specific control is a redaction processor, a small function that inspects every
entry and replaces the value of any field whose name looks sensitive before the
entry is written anywhere. This topic sits in the Security track because logs are
a frequently overlooked path by which private data escapes a system.

**Learning outcomes** — after reading this document you will be able to:

- Explain why logs are a realistic leak path for private data, and why a rule to remember not to log it fails at scale. Human memory is not a reliable control.
- Describe how a central redaction step turns the logging layer into a defense rather than a liability. One function guards every entry.
- Explain why redacting by field name, early in the pipeline, catches mistakes that per-call-site discipline misses. The guard runs regardless of who wrote the log line.
- Recognise the common mistakes when adding a redaction step and recover from them. A misplaced step protects nothing.

Prerequisites:

- [structlog and structured JSON logging](03-backend-07-structlog-and-json-logging.md) — introduces the logging library, the processor chain, and how each entry flows through it.

## Problem it solves

Every running service writes logs, and logs are persistent, widely copied, and
often shipped to third-party collection systems. That makes them a prime place
for private data to leak: a developer adds a debugging line that prints a whole
request body, and now an email address or a chunk of resume text sits in a log
store, replicated to backups and searchable by anyone with log access. The
concrete problem is that sensitive values reach the logs not through malice but
through ordinary, well-meaning code written under deadline.

The usual first approach is a rule: "never log private data." That rule is
correct but unenforceable by goodwill alone. It depends on every author, in every
file, remembering it every time — and a single forgotten line in one rarely
touched code path leaks the data anyway. The rule also rots: a field that was
harmless last year starts carrying an email address after a refactor, and the
log line nobody revisited now leaks.

Structured logging makes a better defense possible. Because every entry is a set
of named fields rather than a sentence, a single function can inspect the field
names of every entry and scrub the values that look sensitive — before anything
is written. The privacy rule stops being a habit each author must remember and
becomes a property of the system that holds no matter who wrote the log call.

## Mental model

Think of an office that shreds sensitive pages before any document leaves the
building. Every outgoing document passes one desk by the exit. The clerk there
does not read for meaning; they scan each page's labelled fields and, wherever a
field is labelled "Social Security number" or "home address", they black it out
with a marker before the document continues to the mailroom. It does not matter
which department wrote the document or whether they remembered the privacy policy:
the marker desk sits on the only path out, so nothing sensitive leaves unredacted.

When one log entry flows through the system, the steps are:

1. The code calls the logger with an event name and some named fields, one of which might accidentally hold a sensitive value.
2. A merge step adds shared context fields, such as an identifier for the current request.
3. The redaction step walks every field, and for each field whose name matches a sensitive pattern, replaces its value with a fixed placeholder.
4. Later steps add standard fields like the severity level and a timestamp.
5. The final step serialises the now-scrubbed entry and writes it out, so no sensitive value was ever present in the written bytes.

Step 3 is the privacy control. Because it runs before step 5, the sensitive value
is gone before the entry is ever turned into output.

## How it works

Structured logging represents each log entry as a dictionary of named fields that
passes through an ordered sequence of small functions — a processor chain — before
it is written. A redaction processor is one function placed early in that chain.
It receives the entry's fields and examines each field's name against a pattern of
sensitive fragments — names containing fragments such as "password", "token",
"secret", or "email". When a field name matches, the processor overwrites that
field's value with a single fixed placeholder string and leaves the rest
untouched. A thorough implementation recurses into nested structures, so a
sensitive field buried inside a nested object is caught too.

Two design choices make this a genuine defense rather than a fig leaf. The first
is matching by field name rather than by value. The processor does not try to
recognise what an email address or a token looks like — an unreliable and
expensive task. It trusts the field name: if a field is called `email`, its value
is treated as sensitive regardless of content. This is why naming conventions
matter — a value smuggled into a vaguely named field can slip past, so the pattern
is written to match common name fragments broadly.

The second choice is position. The redaction step is placed near the front of the
chain, before any step that serialises the entry into its final written form. If
redaction ran after serialisation, the bytes carrying the sensitive value would
already exist and the scrub would be too late. Running it early guarantees that
every later step — and every renderer, whether human-readable or machine-readable
— only ever sees the scrubbed entry.

The result is defense in depth: the primary rule ("don't log private data") still
stands, but the redaction processor is a backstop that catches the inevitable
mistakes. Using a single, recognisable placeholder also turns an accidental leak
into a signal — a search for that placeholder across the logs reveals exactly
which call sites tried to log something sensitive, so the underlying mistake can
be fixed at the source.

## MatchLayer Phase 1 usage

The redaction policy lives in `apps/api/src/matchlayer_api/core/logging.py`. A
compiled pattern lists the field-name fragments treated as sensitive; any field
whose name contains one of these, case-insensitively, is scrubbed:

Source: `apps/api/src/matchlayer_api/core/logging.py`

```python
_PII_KEY_PATTERN = re.compile(
    r"password|token|secret|email|resume_text|parsed_text",
    re.IGNORECASE,
)
```

The replacement is a single, greppable sentinel so an accidental leak is both
blocked and easy to find afterward:

Source: `apps/api/src/matchlayer_api/core/logging.py`

```python
_REDACTED_VALUE = "***REDACTED***"
```

The scrubbing function recurses into nested mappings and lists so a sensitive
field nested inside a larger payload is still caught:

Source: `apps/api/src/matchlayer_api/core/logging.py`

```python
def _scrub_in_place(value: Any) -> None:
    # ``Any`` is unavoidable here: structlog event dicts carry arbitrary
    # caller-supplied values. ``conventions.md`` permits ``Any`` with an
    # explicit justification, which this comment supplies.
    if isinstance(value, MutableMapping):
        for key in list(value):
            child = value[key]
            if isinstance(key, str) and _PII_KEY_PATTERN.search(key):
                value[key] = _REDACTED_VALUE
                continue
            _scrub_in_place(child)
    elif isinstance(value, list):
        for item in value:
            _scrub_in_place(item)
```

The redaction step is placed in the processor chain immediately after the
context-merge step and before any renderer, so the scrub always runs before an
entry is serialised. The exact ordering of the full chain is covered in the
prerequisite document linked in the Introduction.

## Common pitfalls

- **Mistake:** Relying on each call site to leave sensitive values out instead of redacting centrally.
  **Symptom:** A private value — an email address, a token, a chunk of resume text — appears in the logs after a single author forgets the rule in one place.
  **Recovery:** Add a redaction processor near the front of the chain that scrubs values by field name, so the policy holds for every entry no matter who wrote the call.

- **Mistake:** Placing the redaction step after a renderer, or after a step that has already serialised the entry.
  **Symptom:** Sensitive values still appear in the written output because the bytes were produced before the scrub ran.
  **Recovery:** Order the chain so redaction runs before any renderer, and confirm by logging a deliberately named sensitive test field and checking the output shows only the placeholder.

- **Mistake:** Smuggling a sensitive value into a vaguely named field (for example, logging it under `detail` or `data`) so the name-based matcher does not catch it.
  **Symptom:** The redaction step runs but the value leaks anyway because its field name matched no sensitive fragment.
  **Recovery:** Name fields for what they hold, keep the sensitive-fragment pattern broad, and never pour mixed free-form content into a generically named field.

## External reading

- [structlog documentation: processors](https://www.structlog.org/en/stable/processors.html)
- [Python documentation: the logging facility](https://docs.python.org/3/library/logging.html)
- [Python documentation: regular expressions](https://docs.python.org/3/library/re.html)
- [OWASP Logging Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html)
