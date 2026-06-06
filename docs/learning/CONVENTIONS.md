# MatchLayer Learning Docs — Conventions

This document is the schema for the MatchLayer Learning Docs library. Every rule that a reviewer or the compliance validator (`tools/learning_docs_check.py`) enforces against a Topic_Doc traces back to a section here. A Topic_Doc is a single Markdown file inside a phase sub-library (for Phase 1, `docs/learning/phase-1/`) that explains one topic in depth for the Reader defined below.

Read this document before authoring or updating any Topic_Doc. The sections are ordered the way an author meets them: who you are writing for, the template you fill in, the naming and heading rules, how to cite files and code, how to link, the beginner-accessibility bar, when to add the optional hands-on exercise, how to keep docs in sync with code, and who owns the library.

Two entry-point files — this Conventions_Doc and the Library_Index at `docs/learning/README.md` — are exempt from the Topic_Doc template rules, because they are indexes and schemas rather than topic explanations. The rules below apply to Topic_Docs unless a section states otherwise.

## Reader definition

Every Topic_Doc is written for **the Reader**. The Reader is a junior developer with:

- **no prior exposure to the topic** the Topic_Doc covers,
- **no prior exposure to the topic's prerequisite topics**, and
- **no assumed familiarity with the project's chosen stack** (the frameworks, libraries, protocols, and tools MatchLayer uses).

The Reader is the calibration anchor for every beginner-accessibility rule in this document. When a sentence assumes knowledge the Reader does not have, that sentence is a defect. Write so that a junior developer with zero prior knowledge of the topic can follow the document from the first line to the last without leaving it to learn a prerequisite that was never named.

## Doc_Template

Every Topic_Doc follows one fixed structure: an H1 title on line 1, followed by exactly **seven required H2 sections in this exact order**, plus **one optional H2 section** (`Hands-on checkpoint`) that, when present, is placed strictly between `Common pitfalls` and `External reading`.

The required order is:

1. `Introduction`
2. `Problem it solves`
3. `Mental model`
4. `How it works`
5. `MatchLayer Phase 1 usage`
6. `Common pitfalls`
7. `External reading`

The optional `Hands-on checkpoint` is the **only** permitted variation in the section list. No other section may be added, removed, renamed, or reordered. A Topic_Doc that omits `Hands-on checkpoint` is fully compliant as long as all seven required sections are present in the order above.

Skeleton (heading structure only — the title and prose are filled in per topic):

```text
# <Topic title>

## Introduction

## Problem it solves

## Mental model

## How it works

## MatchLayer Phase 1 usage

## Common pitfalls

## Hands-on checkpoint        (optional — only between Common pitfalls and External reading)

## External reading
```

### Section-by-section requirements

| #   | Required? | Heading text               | What the section must contain                                                                                                                                                                                                                         |
| --- | --------- | -------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0   | yes       | _Topic title_ (H1)         | Human-readable title, 3–80 characters, on line 1, followed by a blank line.                                                                                                                                                                           |
| 1   | yes       | `Introduction`             | Introduces the topic in plain language. Contains an explicit statement of **at least three learning outcomes**, each phrased as a single declarative sentence. Declares prerequisite Topic_Docs as a hyperlinked list (or states "No prerequisites"). |
| 2   | yes       | `Problem it solves`        | Names **at least one concrete problem** the topic addresses, and describes **at least one prior approach or pre-existing state** that existed before the topic was adopted.                                                                           |
| 3   | yes       | `Mental model`             | Contains **at least one** concrete handhold: a named analogy to a familiar real-world concept, a diagram (image, ASCII, or Mermaid), or a numbered step-by-step walkthrough of **at least three steps**. Appears before any deep technical detail.    |
| 4   | yes       | `How it works`             | Explains the topic **conceptually**. **MUST NOT reference any MatchLayer-specific file, module, directory, or Phase 1 implementation detail.** Keep this section reusable by anyone learning the topic, independent of MatchLayer.                    |
| 5   | yes       | `MatchLayer Phase 1 usage` | Contains **at least one** reference to an Implementation_File path (repository-root-relative) and **at least one** fenced code snippet copied verbatim from that file, with the `Source:` citation line. This is where MatchLayer specifics live.     |
| 6   | yes       | `Common pitfalls`          | Lists **at least three distinct pitfalls**. Each pitfall states three labelled parts: **Mistake**, **Symptom** (an observable sign by which the Reader recognizes it), and **Recovery** (the action that fixes it).                                   |
| 7   | optional  | `Hands-on checkpoint`      | A time-bounded exercise of **no more than 30 minutes**. Included only when it materially reinforces the topic per the Hands-on checkpoint rubric below. Placed immediately before `External reading`.                                                 |
| 8   | yes       | `External reading`         | Lists **1 to 10 external hyperlinks**, each an authoritative source per the Link rules. At least one resource is official project documentation, a published book (with author and title), or a canonical blog post (with author and URL).            |

### Why `How it works` and `MatchLayer Phase 1 usage` are separate

`How it works` is the topic taught in the abstract; `MatchLayer Phase 1 usage` is how MatchLayer applies it. Keeping the conceptual explanation free of MatchLayer file paths and product references means the Reader learns the transferable idea first, then sees the concrete application second. A reference to any repository path (for example, a path beginning with `apps/`, `packages/`, `infra/`, `ml/`, `tools/`, `.kiro/`, or `docs/`) or to the product name inside `How it works` is a compliance violation and must be moved into `MatchLayer Phase 1 usage`.

## Filename rules

- Topic_Doc filenames use **kebab-case** and match the regular expression `^[a-z0-9]+(-[a-z0-9]+)*\.md$` — lowercase letters and digits, hyphen-separated, ending in `.md`. Examples: `docker-and-compose.md`, `03-backend-01-fastapi-application-factory.md`, `08-matching-02-tf-idf-and-cosine-similarity.md`.
- The maximum filename length is **80 characters including the `.md` extension**.
- The reserved name **`README.md` is excluded** — it is the phase index, not a Topic_Doc.
- Every Topic_Doc is a single Markdown file placed **directly inside** the phase sub-library directory. There are **no subdirectories** under `docs/learning/phase-1/`.

A filename that does not match the pattern marks the Topic_Doc non-compliant; the spec is not closed until it is corrected.

## Heading rules

- **H1 on line 1.** Line 1 is exactly one H1 heading (`# `) whose text is the human-readable topic title, between **3 and 80 characters**. Line 2 is blank (at least one blank line follows the H1 before any other content).
- **Doc_Template sections are H2.** Every section that maps to a Doc_Template section uses an H2 heading (`## `) whose text matches the section name **exactly** — including capitalization, spacing, and punctuation. `## Problem it solves` is correct; `## Problem It Solves` and `## Problem-it-solves` are not.
- **Exact required order, no deviation.** The seven required sections appear in this exact order with no omissions, additions, or reordering: `Introduction`, `Problem it solves`, `Mental model`, `How it works`, `MatchLayer Phase 1 usage`, `Common pitfalls`, `External reading`.
- **Optional section placement.** When the optional `Hands-on checkpoint` section is included, its H2 heading appears **immediately after the last content line of `Common pitfalls` and immediately before the H2 heading of `External reading`**, with no other H2 section between them.

Authors may use H3 (`### `) and deeper headings **inside** a Doc_Template section for sub-structure; only the H2 level is constrained to the template section names.

A Topic_Doc that is missing a required section, orders the required sections differently, or uses a heading whose text does not match a Doc_Template section name exactly is non-compliant. The violation is surfaced with the offending Topic_Doc and the specific rule it breaks, and the spec is not closed until it is fixed.

## File-reference rules

Every reference to an **Implementation_File** (any source, configuration, or scaffolding file in the MatchLayer repository that a Topic_Doc cites by path) uses the file's **POSIX-style path relative to the repository root**:

- **no leading slash** (`apps/api/src/matchlayer_api/main.py`, not `/apps/api/...`),
- **no `./` prefix** (`tools/check_env_drift.py`, not `./tools/check_env_drift.py`),
- forward slashes only.

Example of a correctly formatted reference: `apps/api/src/matchlayer_api/main.py`.

Every Implementation_File path in a Topic_Doc must **resolve to an existing file** in the repository on the branch where the Topic_Doc is committed. A path that does not resolve marks the Topic_Doc non-compliant and blocks closure of the spec until every reference resolves to a real file.

## Code-snippet rules

- **Fenced blocks only.** Every code snippet is enclosed in a Markdown fenced code block opened and closed with three backticks.
- **One allowed language tag.** The opening fence carries exactly one lowercase language identifier from this canonical set:

  ```yaml
  allowed_languages:
    - python
    - typescript
    - tsx
    - javascript
    - jsx
    - yaml
    - json
    - dockerfile
    - sql
    - bash
    - sh
    - text
  ```

  Use `text` for plain output, directory trees, and skeletons that are not a specific language.

- **Verbatim from source.** A snippet copied from an Implementation_File is reproduced **character-for-character** from the current state of that file on the branch where the Topic_Doc is committed. The **only** permitted edit is **removing whole lines** — never altering, reformatting, or paraphrasing the lines that remain. (Formally: the snippet body must be a whole-line subsequence of the source file.)
- **Citation line.** Every snippet copied from an Implementation_File is immediately preceded or followed by a single citation line of the form `` Source: `<path>` `` where `<path>` is the repository-root-relative POSIX path of the source file. For example, a snippet from the FastAPI entry point carries the citation line: Source followed by the backtick-quoted path `apps/api/src/matchlayer_api/main.py`.
- **Simplified snippets are labelled.** A snippet that is **not** a verbatim copy of an Implementation_File (a teaching example, a reduced sketch, or invented illustrative code) is **preceded by a line containing the exact phrase `simplified for illustration`** and **followed by a Markdown hyperlink whose target is the repository-root-relative path of the closest real source file**. This keeps the Reader from mistaking an illustrative sketch for the actual repository contents.

A snippet in `MatchLayer Phase 1 usage` whose contents do not match the referenced Implementation_File at authoring time marks the Topic_Doc non-compliant; the spec is not closed until it is corrected.

## Link rules

- **Internal links are relative and must resolve.** Every internal hyperlink uses a **relative path** — no scheme, no host, no leading `/` — that resolves to an existing file or heading anchor when interpreted from the directory containing the Topic_Doc. A broken internal link (target path missing at the commit) marks the Topic_Doc non-compliant and is reported with its source file, line number, and target path; the spec is not closed until it resolves.
- **Heading anchors must exist.** An internal link to a heading anchor (a fragment beginning with `#`) must match a heading actually present in the target file. A dangling anchor marks the Topic_Doc non-compliant and must be corrected before closure.
- **External links use `https://` exactly.** Every external hyperlink uses the `https://` scheme. Links using `http://`, protocol-relative `//`, or any other scheme are non-compliant.
- **Prefer authoritative sources.** An **authoritative source** for an external link is one of: the official documentation site of the referenced product, library, or service; the official specification published by its standards body; the project's own first-party website; or a canonical reference site listed in the host registry below. When an authoritative source exists for a topic, the Topic_Doc **must link to it** rather than to a secondary tutorial, blog post, or content aggregator.
- **`External reading` bounds.** The `External reading` section lists **at least one and at most ten** external hyperlinks, each satisfying the https-scheme and authoritative-source rules above.

### Authoritative-host registry

The validator reads this list from the fenced YAML block below, so adding an authoritative source is a docs-only change. The registry includes at minimum MDN Web Docs (`developer.mozilla.org`), the Python documentation (`docs.python.org`), and the Next.js documentation (`nextjs.org`):

```yaml
authoritative_hosts:
  - developer.mozilla.org # MDN Web Docs
  - docs.python.org # Python language and standard library
  - nextjs.org # Next.js
  - fastapi.tiangolo.com # FastAPI
  - docs.pydantic.dev # Pydantic
  - docs.sqlalchemy.org # SQLAlchemy
  - alembic.sqlalchemy.org # Alembic
  - docs.docker.com # Docker
  - www.postgresql.org # PostgreSQL
  - redis.io # Redis
  - docs.astral.sh # uv and Ruff
  - pnpm.io # pnpm
  - nodejs.org # Node.js
  - www.typescriptlang.org # TypeScript
  - tailwindcss.com # Tailwind CSS
  - ui.shadcn.com # shadcn/ui
  - hypothesis.readthedocs.io # Hypothesis
  - docs.pytest.org # pytest
  - vitest.dev # Vitest
  - playwright.dev # Playwright
  - scikit-learn.org # scikit-learn
  - pypdf.readthedocs.io # pypdf
  - python-docx.readthedocs.io # python-docx
  - docs.github.com # GitHub Actions and platform docs
  - www.w3.org # W3C specifications (including WCAG)
  - datatracker.ietf.org # IETF RFCs
```

## Beginner-accessibility ruleset

Every Topic_Doc is written for the Reader (see **Reader definition**). The following rules are mandatory.

- **Define every domain-specific term on first use.** A domain-specific term is any noun or noun phrase that is not part of general programming vocabulary — framework names, protocol names, pattern names, and project-internal concepts. Define each one **on its first use within that Topic_Doc**, with the definition appearing **in the same paragraph** as the first use, even when the term is defined elsewhere in the library.
- **Expand every acronym on first use.** Introduce each acronym in expanded form on first use as `Expanded Form (ACRONYM)` — for example, "Cross-Origin Resource Sharing (CORS)" or "JSON Web Token (JWT)". The bare acronym may be used only after that first expanded occurrence within the same Topic_Doc.
- **Mental model before technical detail.** The `Mental model` section appears before any deep technical detail and includes at least one concrete handhold: an analogy to a familiar real-world concept, a diagram, or a numbered step-by-step walkthrough.
- **Declare prerequisites in the Introduction.** The `Introduction` declares the Topic_Doc's prerequisite Topic_Docs as an explicit list, each prerequisite hyperlinked to its Topic_Doc. A prerequisite is any Topic_Doc covering a concept the current Topic_Doc references without defining inline. When there are none, state "No prerequisites" explicitly.
- **Reference-or-define on cross-mention.** When a Topic_Doc references a concept covered in another Topic_Doc, it either includes a brief inline definition of one to three sentences **or** hyperlinks to the relevant Topic_Doc. A reference that does neither is prohibited.
- **No knowledge-presuming phrases.** The constructions in the banned-phrase list below are prohibited outside fenced code blocks, along with any phrase asserting that the Reader already understands a concept that has not been defined earlier in the same Topic_Doc.

### Project glossary (define-on-first-use list)

The validator flags the first in-prose use of each glossary term that is not accompanied by a same-paragraph definition. Treat this as the minimum set; define any other domain-specific term the same way. The canonical glossary terms are:

```text
monorepo
workspace
lockfile
pre-commit hook
Server Component
Client Component
strict mode
design token
application factory
ASGI
async / await
event loop
session factory
connection pool
migration
structured logging
middleware
request id
JSON Web Token (JWT)
access token
refresh token
token rotation
Argon2id
CSRF
rate limiting
audit log
account enumeration
TF-IDF
cosine similarity
skill lexicon
magic-byte validation
zip bomb
storage abstraction
UUIDv7
soft delete
cursor pagination
idempotency key
container
image layer
multi-stage build
healthcheck
distroless
OpenAPI
codegen
property-based testing
fixture
accessibility (axe-core)
import boundary
CI job
branch protection
```

### Banned-phrase list

These knowledge-presuming phrases are prohibited outside fenced code blocks (case-insensitive, whole-word match):

```text
as you know
obviously
clearly
simply
just
of course
everyone knows
it should be clear
```

`just` is **advisory**: it has legitimate non-presuming uses ("a just-in-time compiler", "just below the threshold"), so the validator emits it as a warning for reviewer triage rather than as a hard failure. Every other phrase is a hard finding.

### Author self-check and reviewer gate

- **Author self-check.** Before marking a Topic_Doc ready for review, the author evaluates every section against the rules in this ruleset (define-on-first-use, acronym expansion, mental-model handhold, prerequisite declaration, reference-or-define on cross-mention, banned phrases) and revises any section that fails.
- **Reviewer gate.** If a Topic_Doc under review contains a term, acronym, prerequisite reference, or piece of jargon that does not satisfy the define-on-first-use, acronym-expansion, prerequisite-declaration, or reference-or-define rules, the reviewer **rejects** the Topic_Doc and **cites the specific failing rule**.

## Hands-on checkpoint rubric

`Hands-on checkpoint` is **optional**. Include it only when a short, practical exercise materially reinforces the topic; otherwise omit it (omission keeps the Topic_Doc fully compliant).

Include a `Hands-on checkpoint` when **all** of the following hold:

1. The exercise is **time-bounded to no more than 30 minutes** for the Reader.
2. The exercise produces an **observable artifact** — a command output, a file, a passing test, a rendered page, or a screenshot — that confirms the Reader did it correctly.
3. The artifact **reinforces the topic's `How it works` or `MatchLayer Phase 1 usage` content**, not unrelated material.

Apply this threshold when deciding:

- **Three or more** candidate exercises meeting the criteria above → inclusion is justified; pick the single strongest one.
- **Fewer than two** viable candidate exercises → omit the section.

When included, the section is placed immediately before `External reading` and states the time box, the steps, and the expected observable artifact.

## Maintenance discipline

Learning docs must stay in sync with the code they explain. The governing rule is **same-PR updates**:

- **Same-PR update rule.** Any change to an Implementation_File, or to a library or technology covered by a Topic_Doc, **must include the corresponding Topic_Doc update in the same pull request, before merge**.
- **Additions and replacements.** When a pull request introduces a new Implementation_File covered by a Phase 1 topic, adds a new library or technology referenced by an existing Topic_Doc, or replaces a library or technology referenced by an existing Topic_Doc, that pull request updates or adds the corresponding Topic_Doc in the same pull request before merge.
- **Removals.** When a pull request removes an Implementation_File covered by a Topic_Doc, or removes a library or technology covered by a Topic_Doc, that pull request updates or removes the corresponding Topic_Doc in the same pull request before merge.
- **Snippet freshness.** When a pull request modifies an Implementation_File referenced by one or more Topic_Docs, it updates each affected Topic_Doc in the same pull request so that every reference still resolves and every verbatim snippet matches the post-merge file content character-for-character.
- **Merge block.** A pull request that meets the addition, replacement, or removal conditions above but does not include the corresponding Topic_Doc change is **blocked from merging** until either the Topic_Doc change is added, or a written waiver naming the responsible reviewer is recorded in the pull request description.

## Maintainer

- The maintainer of the Phase_1_Sub_Library (`docs/learning/phase-1/`) is the spec author, **`hhumoham`**.
- Maintainership of the Phase_1_Sub_Library **transfers only via a future spec** that names the new maintainer by name or handle. It does not transfer informally.
- Any future phase sub-library (`docs/learning/phase-2/` and later) **must name its maintainer** by name or handle in this Conventions_Doc **before that sub-library is merged**.
