# PII Redaction Policy (Phase 3 — LLM Layer)

This document is the committed, reviewable statement of what the
`PII_Redactor` (`apps/api/src/matchlayer_api/services/llm/redaction.py`)
redacts before any text is transmitted to a third-party LLM provider, what it
deliberately does **not** redact (the **Redaction_Exception**), and the
boundary rule that makes the exempt/redactable classification of any text
span decidable. It fulfils the `security.md` requirement that redaction
exceptions are documented explicitly, and requirements 3.1–3.7 of the
`phase-3-llm-layer` spec.

The behavior described here is versioned: the module exports
`REDACTOR_VERSION` (currently `1.0.0`), and every LLM invocation log records
the redactor version that produced its input, so Phase 5 evaluation replay
can reproduce the exact redacted text. Any change to the patterns, lexicons,
heuristic, or boundary rule below requires a version bump.

## What is redacted

Three PII types are detected and replaced with **indexed typed
placeholders** — `[EMAIL_n]`, `[PHONE_n]`, `[NAME_n]`:

| Type               | Detection                                                      | Scope                                                                                   |
| ------------------ | -------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| Email addresses    | Committed regex (`_EMAIL_RE`)                                  | Entire text, all input kinds                                                            |
| Phone numbers      | Committed regex (`_PHONE_RE`, common US/international formats) | Entire text, all input kinds                                                            |
| Obvious full names | Committed heuristic over the contact/header region (below)     | Detected in the header of resumes; redacted at **every** occurrence throughout the text |

Placeholder indexing rules (requirements 3.1, 3.5):

- One index per **distinct detected value** per type. Distinctness is exact
  string equality of the matched text — e.g. `555-123-4567` and
  `(555) 123-4567` are distinct values even if they denote the same number.
- Indices start at **1** per type and are assigned in **first-replaced-
  occurrence order** within the text (a value whose occurrences are all
  inside exempt regions is never replaced and never consumes an index, so
  indices are gap-free).
- Every occurrence of the same value receives the **same** placeholder, so
  the redacted text stays internally coherent for the LLM.

Overlapping detections are resolved deterministically by type precedence
`EMAIL > PHONE > NAME`, earlier occurrence first within a type; a
lower-precedence span overlapping an accepted span is dropped.

## The name heuristic (committed)

Applied only to resume input (`kind="resume"`), over the **contact/header
region** (defined below):

1. Each non-empty header line is tokenized into word tokens
   (`[A-Za-z][A-Za-z'’.-]*`).
2. A candidate name is a **maximal run of 2–4 consecutive capitalized
   tokens** — first character uppercase — separated only by spaces/tabs,
   where **no token** of the run is (case-insensitively) in the committed
   exclusion lexicon `_NAME_EXCLUSION_WORDS` (section-heading words plus
   common job-title and contact-label words such as _senior_, _engineer_,
   _email_, _linkedin_). Runs of 1 token or more than 4 tokens are not
   names.
3. Each detected name string is then matched at **every** word-bounded
   occurrence in the whole text — not only in the header — and redacted
   there too (requirement 3.2), subject to the Redaction_Exception below.

Matching of detected names elsewhere in the text is exact and
case-sensitive.

## The Redaction_Exception

**Employment-history entries and company names are transmitted exactly as
written, without redaction**, even when they contain values matching the
email/phone patterns or a detected name.

**Rationale.** The three Phase 3 features (resume coach, bullet rewriting,
interview question generation) reason about _where the candidate worked,
what they did there, and how that maps to the target job_. Employer names,
role titles, and the concrete content of employment entries are the raw
material of that advice; replacing them with placeholders measurably
degrades coaching quality (generic advice detached from the actual career
history) while providing little privacy benefit — employment history is the
content the user is explicitly asking the LLM to analyze. Direct contact
identifiers (emails, phone numbers, the candidate's name in the header)
carry no coaching signal, so those are redacted everywhere else.
`security.md` permits redaction exceptions when documented explicitly; this
document is that record.

## The boundary rule (committed, decidable)

Whether any given text span is exempt or redactable is decided purely from
the text and the input kind, as follows.

### Line and heading normalization

The text is split into lines on `\n`. A line is normalized for heading
matching by: stripping surrounding whitespace, stripping the decoration
characters `#`, `*`, `=`, `-`, `–`, `—` from both ends, removing one
trailing `:`, stripping again, and lowercasing.

A line is a **recognized section heading** iff its normalized form is
exactly an entry of the committed `SECTION_HEADINGS` lexicon (e.g.
`summary`, `education`, `skills`, `projects`, `certifications`, plus the
employment headings below — full list in `redaction.py`).

### Employment-history sections

A line is an **employment-history heading** iff its normalized form is
exactly one of the committed `EMPLOYMENT_SECTION_HEADINGS` lexicon:

> `experience`, `work experience`, `employment`, `employment history`,
> `work history`, `professional experience`

An **employment-history section** starts at its heading line and extends to
the start of the next recognized section heading (any lexicon entry), or to
the end of the text if none follows.

**A span is exempt iff it lies fully inside an employment-history section of
a resume.** A span that starts before or ends after the section boundary is
redactable. This containment test is the entire boundary rule for the
Redaction*Exception: for the exception's purposes, an "employment history
entry or company name" is exactly \_any text inside an employment-history
section as delimited above*. Company names mentioned outside such a section
(e.g. in a summary) are not detected by any pattern — the redactor detects
only emails, phones, and header-derived names — so they are naturally
preserved as well; if a header-detected name, email, or phone appears
outside an employment-history section, it **is** redacted regardless of
whether it happens to also be a company name.

### The contact/header region

The contact/header region of a resume is the run of lines before the first
recognized section heading, capped at the **first 10 lines** of the text
(whichever boundary comes first). It is the only region the name heuristic
scans for candidates.

### Input kinds

`redact()` takes `kind: "resume" | "job_description" | "bullet"`:

- **`resume`** — full algorithm: segmentation, header-region name
  heuristic, employment-history exemption, whole-text email/phone
  redaction.
- **`job_description`** and **`bullet`** — whole-text email/phone redaction
  only, with **no exemptions** and no name heuristic. These inputs are not
  resume-shaped: a job description's "Experience" section lists
  requirements, not the candidate's employment history, and exempting it
  would leak recruiter contact details; the name heuristic over the top of
  a job description or a bullet would misclassify job titles as names.
  Company names appearing in these inputs are preserved automatically
  because nothing detects them.

## Failure behavior and PII discipline

- The whole redaction runs inside a **5-second wall-clock bound**. Any
  internal error or timeout raises `RedactionError`, whose message is a
  fixed string carrying **no fragment of the input**; the affected request
  takes the Fallback_Response path and **nothing unredacted is ever
  transmitted** (requirement 3.6).
- The redactor **never logs**: neither its input nor its output appears in
  any log line, error message, or telemetry signal (requirement 3.7).
- All downstream hashing — LLM cache keys and invocation-log input hashes —
  is computed **only over redacted text**, never the raw input
  (requirement 3.8).
- Redaction is deterministic and pure: identical input text produces
  identical redacted output under the same `REDACTOR_VERSION`
  (requirement 3.4).

## Known limitations (accepted for v1.0.0)

- The name heuristic targets _obvious_ full names in the resume header. A
  name appearing only deep in the body (never in the header) is not
  detected. Names are also not detected in job descriptions or bullets.
- The phone regex favors recall over precision: long digit sequences that
  are not phone numbers (e.g. 8+-digit identifiers) may be redacted as
  `[PHONE_n]`. Over-redaction is the safe failure direction.
- Distinct formattings of the same phone number receive distinct indices
  (exact-string value identity keeps the redactor deterministic and
  reviewable).
