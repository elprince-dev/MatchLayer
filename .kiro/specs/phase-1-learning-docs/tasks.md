# Implementation Plan: phase-1-learning-docs

## Overview

This plan turns the design into an ordered set of coding tasks. The deliverable is a folder of long-form Markdown under `docs/learning/` plus a Python compliance validator under `tools/`. Work proceeds in five waves:

1. Stand up the empty `docs/learning/` skeleton and the Library_Index, Conventions_Doc, and Phase_1_Index entry points so every later Topic_Doc has a place to land and a schema to follow.
2. Implement the `tools/learning_docs_check.py` validator incrementally — parser first, then each rule (LDC001–LDC020) — with property tests (one per design property P1–P35) added next to the rule that enforces them. The validator drives compliance for every subsequent task.
3. Author the Phase 1 Topic_Docs in twelve thematic batches (Foundation and tooling → Frontend → Backend → Security → Database and storage → Containerization → Contracts and codegen → Hosting and deploy → Authentication and accounts → Matching and scoring → API and data conventions → Testing and quality), running the validator clean after each batch.
4. Wire the Phase_1_Index `Topic coverage` table and `Recommended reading order` to the final filename set, then run the full validator against the whole library.
5. Final checkpoint and developer ergonomics (a `make`-style script entry, a short authoring guide in the spec).

Implementation language for the validator: **Python 3.13** with stdlib only at runtime (`pathlib`, `re`, `json`, `dataclasses`, `argparse`); `pytest` + `hypothesis` as dev dependencies under `tools/`. This matches `tech.md` and the design's stated dependency constraint.

## Tasks

- [x] 1. Bootstrap the Learning_Docs_Library skeleton and entry-point files
  - [x] 1.1 Create the on-disk folder structure for the library
    - Create the directories `docs/learning/` and `docs/learning/phase-1/` as tracked directories (add a `.gitkeep` to `docs/learning/phase-1/` only if needed to commit the empty folder; remove it once the first Topic_Doc lands).
    - Do not introduce subdirectories under `docs/learning/phase-1/`.
    - _Requirements: 1.1, 2.1_

  - [x] 1.2 Author `docs/learning/README.md` (Library_Index)
    - Include H1 `# MatchLayer Learning Docs`, an `Introduction` paragraph, and the H2 sections `Reader Assumption`, `Phase Sub-Libraries`, `External Sources`, and `Non-goals` (or `What this is not`) in this order.
    - `Reader Assumption` body must contain the verbatim sentence "a junior developer with zero prior knowledge of the topic".
    - `Phase Sub-Libraries` lists every direct child `phase-<N>/` directory present (Phase 1 only at this point) with relative Markdown links.
    - `External Sources` includes relative Markdown links to `.kiro/steering/`, `docs/adr/`, and every existing `apps/*/README.md` (at minimum `apps/api/README.md` and `apps/web/README.md` — only include each if the file actually exists; the validator will flag broken targets).
    - `Non-goals` contains at least four bullets covering: not an API reference (link to the committed OpenAPI document path), not a per-app README replacement (one bullet per existing `apps/*/README.md`), not the ADRs (link to `docs/adr/`), and the long-form companion to `.kiro/steering/` (link to that folder).
    - Do not reproduce contiguous 50-word blocks from any file under `.kiro/steering/`, `docs/adr/`, or `apps/*/README.md`; link to the source instead.
    - _Requirements: 1.2, 1.4, 1.5, 1.7, 1.8, 10.1, 10.2, 10.3, 10.4, 10.5, 10.7_

  - [x] 1.3 Author `docs/learning/CONVENTIONS.md` (Conventions_Doc)
    - Include the H2 sections in this order: `Reader definition`, `Doc_Template`, `Filename rules`, `Heading rules`, `File-reference rules`, `Code-snippet rules`, `Link rules`, `Beginner-accessibility ruleset`, `Hands-on checkpoint rubric`, `Maintenance discipline`, `Maintainer`.
    - `Doc_Template` enumerates the seven required H2 sections in exact order with the optional `Hands-on checkpoint` placed strictly between `Common pitfalls` and `External reading` when present, and explicitly forbids any reference to MatchLayer-specific files in the `How it works` section.
    - `Code-snippet rules` lists the allowed languages `{python, typescript, tsx, javascript, jsx, yaml, json, dockerfile, sql, bash, sh, text}`, the verbatim-from-source rule, the `simplified for illustration` labelling rule, and the `` Source: `<path>` `` citation format.
    - `Beginner-accessibility ruleset` defines the project glossary list, the acronym format `Expanded Form (ACRONYM)`, the prerequisite-declaration rule, and the canonical banned-phrase list (`as you know`, `obviously`, `clearly`, `simply`, `just`, `of course`, `everyone knows`, `it should be clear`) with `just` documented as advisory.
    - `Link rules` lists the authoritative-host registry as a fenced YAML block (so the validator can read it) including at minimum `developer.mozilla.org`, `docs.python.org`, and `nextjs.org`.
    - `Maintainer` names the spec author as the Phase_1_Sub_Library maintainer and states maintainership transfers only via a future spec.
    - `Maintenance discipline` states the same-PR update rule for Implementation_File and library/technology changes.
    - _Requirements: 1.3, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 3.10, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 6.1, 6.2, 6.3, 6.5, 6.6, 6.7, 7.1, 7.4, 7.5, 8.1, 8.2, 8.3, 8.4, 8.5, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

  - [x] 1.4 Author the initial `docs/learning/phase-1/README.md` (Phase_1_Index)
    - Include H1 `# MatchLayer Phase 1 — Learning Docs` followed by a blank line.
    - `Introduction` H2: 40–150 words, contains the literal substring `.kiro/specs/phase-1-foundation/`, and identifies that spec as the implementation source of truth.
    - `Topic coverage` H2: a Markdown table with columns `Coverage entry`, `Requirement clause`, `Topic_Doc filename`, `Thematic section`. Seed every row from the design's Phase_1_Topic_Coverage_List → Topic_Doc mapping; the Topic_Doc filename column is left empty for any Topic_Doc not yet authored (the validator's `LDC016` will flag any populated row whose filename does not exist).
    - Add the twelve thematic-section H2s in this exact order: `Foundation and tooling`, `Frontend`, `Backend`, `API and data conventions`, `Security`, `Authentication and accounts`, `Database and storage`, `Matching and scoring`, `Testing and quality`, `Containerization`, `Contracts and codegen`, `Hosting and deploy`. Leave their bodies empty for now (entries are added per Topic_Doc as it is authored).
    - Add a `Recommended reading order` H2 with an empty ordered list to be filled in as Topic_Docs are added.
    - _Requirements: 2.2, 2.3, 2.4, 2.8, 4.14_

- [x] 2. Implement the compliance validator (`tools/learning_docs_check.py`)
  - [x] 2.1 Add the validator dev-dependency setup
    - Create `tools/pyproject.toml` (or extend the existing root-level Python tooling config) declaring `pytest` and `hypothesis` as dev dependencies pinned to current major versions.
    - Create `tools/tests/__init__.py` and `tools/tests/test_learning_docs_check.py` as an empty test module.
    - Wire the validator and its tests into the existing `ruff` and `mypy --strict` configuration so the same lint and type rules apply.
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2_

  - [x] 2.2 Implement the data models and Markdown parser
    - In `tools/learning_docs_check.py`, define the frozen dataclasses `TopicDoc`, `Section`, `FencedBlock`, `Link`, `CoverageRow`, and `Finding` with the fields specified in the design's _Data Models_ section.
    - Implement a `parse_topic_doc(path: Path) -> TopicDoc` function that splits the file into H1, H2 sections, fenced blocks (capturing language, body, line, and `Source:` citation if any), internal vs external links (relative-path heuristic), and a parsed prerequisites list from the `Introduction` section.
    - Implement helpers `walk_library(root: Path) -> Iterable[Path]`, `parse_phase_1_index(path: Path) -> list[CoverageRow]`, and `parse_authoritative_hosts(conventions_path: Path) -> tuple[str, ...]` (reads the YAML block from `CONVENTIONS.md`).
    - _Requirements: 1.1, 1.3, 2.2, 8.2_

  - [x]\* 2.3 Write smoke tests on the parser and library skeleton
    - **Smoke layer (Testing Strategy §Layer 3)**: assert `docs/learning/`, `docs/learning/README.md`, `docs/learning/CONVENTIONS.md`, `docs/learning/phase-1/`, and `docs/learning/phase-1/README.md` exist and are non-empty.
    - Add round-trip tests for `parse_topic_doc` against a fixture Topic_Doc covering H1, every required H2, a sourced fenced block, a `simplified for illustration` block, and a prerequisites list.
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2_

  - [x] 2.4 Implement filename and H1 rules (`LDC001`, `LDC002`)
    - `LDC001`: filename must match `^[a-z0-9]+(-[a-z0-9]+)*\.md$`, length ≤ 80 chars, must not equal `README.md`.
    - `LDC002`: line 1 must be `# <title>` with title length 3–80 chars; line 2 must be blank.
    - _Requirements: 2.9, 8.1, 8.2, 8.6_

  - [x]\* 2.5 Write property test for filename conformance
    - **Property 10: Topic_Doc filename conformance**
    - **Validates: Requirements 2.9, 8.1**

  - [x]\* 2.6 Write property test for Topic_Doc H1 conformance
    - **Property 33: Topic_Doc H1 conformance**
    - **Validates: Requirements 8.2**

  - [x] 2.7 Implement the Doc_Template H2-sequence rule (`LDC003`)
    - Extract H2 headings in source order, drop a single occurrence of `Hands-on checkpoint` only when it appears strictly between `Common pitfalls` and `External reading` with no other H2 in that gap, and assert the residual sequence equals `("Introduction", "Problem it solves", "Mental model", "How it works", "MatchLayer Phase 1 usage", "Common pitfalls", "External reading")`. Emit one finding per deviation, citing the offending heading and line.
    - _Requirements: 3.1, 3.10, 3.11, 8.3, 8.4, 8.5, 8.7_

  - [x]\* 2.8 Write property test for Doc_Template H2 sequence equality
    - **Property 13: Doc_Template H2 sequence equality**
    - **Validates: Requirements 3.1, 3.10, 3.11, 8.3, 8.4, 8.5, 8.7**

  - [x] 2.9 Implement the per-section content rules (`LDC004`, plus content checks for Introduction, Mental model, MatchLayer Phase 1 usage, Common pitfalls, External reading)
    - `LDC004` (`How it works` is implementation-agnostic): outside fenced blocks, the section body must contain no occurrence of `apps/`, `packages/`, `infra/`, `ml/`, `tools/`, `.kiro/`, `docs/`, or the literal `MatchLayer`.
    - `Introduction`: at least three sentences ending in `.` aggregated as a labelled learning-outcomes list or paragraph.
    - `Mental model`: contains at least one of (a) an ordered list with ≥3 items, (b) a fenced `text` ASCII block or `mermaid` block, or (c) a Markdown image reference.
    - `MatchLayer Phase 1 usage`: at least one Implementation_File path reference that resolves under repo root, AND at least one fenced code block whose adjacent `Source:` citation references that or another existing Implementation_File.
    - `Common pitfalls`: at least three distinct entries each containing labelled `Mistake:`, `Symptom:`, and `Recovery:` parts followed by non-empty text.
    - `External reading`: between 1 and 10 Markdown hyperlinks total.
    - _Requirements: 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 5.4, 7.6_

  - [x]\* 2.10 Write property test for Introduction learning outcomes
    - **Property 14: Introduction declares at least three learning outcomes**
    - **Validates: Requirements 3.2**

  - [x]\* 2.11 Write property test for Mental model handhold
    - **Property 15: Mental model section contains a concrete handhold**
    - **Validates: Requirements 3.4, 5.4**

  - [x]\* 2.12 Write property test that "How it works" is implementation-agnostic
    - **Property 16: How it works section is implementation-agnostic**
    - **Validates: Requirements 3.5**

  - [x]\* 2.13 Write property test that MatchLayer Phase 1 usage anchors to an Implementation_File
    - **Property 17: MatchLayer Phase 1 usage section contains anchored content**
    - **Validates: Requirements 3.6**

  - [x]\* 2.14 Write property test for the Common pitfalls labelled-entry rule
    - **Property 18: Common pitfalls section has three labelled entries**
    - **Validates: Requirements 3.7**

  - [x]\* 2.15 Write property test for External reading section size bounds
    - **Property 19: External reading section size bounds**
    - **Validates: Requirements 3.8, 7.6**

  - [x] 2.16 Implement file-reference and code-snippet rules (`LDC005`, `LDC006`, `LDC007`, `LDC008`)
    - `LDC005`: every Implementation_File path in `MatchLayer Phase 1 usage` is POSIX-style, repo-root-relative (no leading `/`, no `./`), and resolves to an existing file.
    - `LDC006`: every fenced block whose surrounding text carries a `Source: <path>` citation has body equal to a whole-line subsequence of the file at `<path>` (no edits other than removing whole lines), and the citation matches the `` Source: `<path>` `` form immediately preceding or following the block.
    - `LDC007`: every fenced block that is not a whole-line subsequence of any Implementation_File must be preceded by the literal phrase `simplified for illustration` and followed by a Markdown hyperlink whose target is a repo-root-relative path that resolves.
    - `LDC008`: every fenced block uses one of the allowed language identifiers from the canonical set.
    - _Requirements: 3.6, 3.12, 6.1, 6.2, 6.3, 6.4, 6.6, 6.7_

  - [x]\* 2.17 Write property test that Implementation_File references are well-formed and resolve
    - **Property 27: Implementation_File reference is well-formed and resolves**
    - **Validates: Requirements 6.1, 6.4**

  - [x]\* 2.18 Write property test that sourced fenced blocks match their source
    - **Property 28: Sourced fenced block matches its source and carries a citation**
    - **Validates: Requirements 6.2, 6.7, 3.12**

  - [x]\* 2.19 Write property test for the simplified-for-illustration block format
    - **Property 29: Simplified-for-illustration block is labelled and linked**
    - **Validates: Requirements 6.3**

  - [x]\* 2.20 Write property test for allowed code-block language tags
    - **Property 30: Fenced code blocks use allowed language tags**
    - **Validates: Requirements 6.6**

  - [x] 2.21 Implement link-integrity rules (`LDC009`, `LDC010`, `LDC011`)
    - `LDC009`: every internal hyperlink resolves to an existing path; every fragment matches a heading present in the target file (using GitHub-style anchor slugs).
    - `LDC010`: every external hyperlink begins with the literal scheme `https://` (rejecting `http://`, protocol-relative `//`, and other schemes).
    - `LDC011`: the `External reading` section has between 1 and 10 external hyperlinks (overlaps with 2.9 but enforced as a dedicated rule for clearer findings).
    - Add an authoritative-source preference check that compares the external-link host against the YAML registry in `CONVENTIONS.md` and emits an advisory finding when a non-authoritative host is used for a topic with a registered authoritative host.
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 10.7_

  - [x]\* 2.22 Write property test for internal hyperlink resolution
    - **Property 11: Internal hyperlink resolution**
    - **Validates: Requirements 2.10, 7.1, 7.2, 10.7**

  - [x]\* 2.23 Write property test for internal heading-anchor matching
    - **Property 12: Internal heading-anchor matches a real heading**
    - **Validates: Requirements 7.3**

  - [x]\* 2.24 Write property test for the https-only external-link scheme
    - **Property 31: External hyperlinks use the https scheme**
    - **Validates: Requirements 7.4**

  - [x]\* 2.25 Write property test for authoritative-source host preference
    - **Property 32: Authoritative source preference is honored**
    - **Validates: Requirements 7.5**

  - [x] 2.26 Implement beginner-accessibility rules (`LDC012`, `LDC013`, `LDC014`, `LDC015`)
    - `LDC012` (heuristic, advisory): every domain-glossary term in the Conventions_Doc list is followed in the same paragraph by a parenthetical, em-dash clause, or copular `is` clause introducing its definition on first use in a Topic_Doc.
    - `LDC013` (heuristic, advisory): every acronym (≥2 capital letters surrounded by word boundaries) is preceded on first use by `Expanded Form (` and followed by `)`.
    - `LDC014`: outside fenced blocks, no banned phrase from the canonical set appears as a case-insensitive whole-word match. `just` is emitted as a warning rather than a hard finding.
    - `LDC015`: the `Introduction` section contains either a hyperlinked Prerequisites list (each link target resolving to another Topic_Doc) or an explicit "no prerequisites" sentence.
    - _Requirements: 5.2, 5.3, 5.5, 5.6, 5.7, 5.8, 5.9_

  - [x]\* 2.27 Write property test for domain-term first-use definitions
    - **Property 22: Domain-specific term defined on first use**
    - **Validates: Requirements 5.2**

  - [x]\* 2.28 Write property test for acronym expanded-form first use
    - **Property 23: Acronym introduced in expanded form on first use**
    - **Validates: Requirements 5.3**

  - [x]\* 2.29 Write property test for absence of banned phrases
    - **Property 24: Banned phrases are absent**
    - **Validates: Requirements 5.5**

  - [x]\* 2.30 Write property test for prerequisites declared in Introduction
    - **Property 25: Prerequisites declared in the Introduction**
    - **Validates: Requirements 5.6**

  - [x]\* 2.31 Write property test for cross-reference handling
    - **Property 26: Cross-reference is hyperlinked or inline-defined**
    - **Validates: Requirements 5.7**

  - [x] 2.32 Implement coverage and Library_Index rules (`LDC016`, `LDC017`, `LDC018`, `LDC019`, `LDC020`)
    - `LDC016`: every row of the Phase_1_Index `Topic coverage` table names a Topic_Doc filename that exists under `docs/learning/phase-1/`; every coverage-list entry from the design has at least one row with a non-empty filename.
    - `LDC017`: every Topic_Doc file under `docs/learning/phase-1/` (other than `README.md`) is listed in exactly one thematic section of the Phase_1_Index, and the link's target equals the filename and the link text equals the Topic_Doc's H1 title.
    - `LDC018`: the Library_Index `Phase Sub-Libraries` section lists every present `phase-<N>/` directory (1 ≤ N ≤ 7).
    - `LDC019`: the Library_Index `External Sources` and `Non-goals` sections reference every existing `apps/*/README.md`, plus `.kiro/steering/` and `docs/adr/`.
    - `LDC020`: every relative link in `Non-goals` and `External Sources` resolves to an existing path.
    - _Requirements: 1.4, 1.7, 2.5, 2.6, 2.10, 4.1, 4.14, 4.16, 10.3, 10.7_

  - [x]\* 2.33 Write property test for the coverage table → Topic_Doc mapping
    - **Property 20: Coverage table maps every entry to an existing Topic_Doc**
    - **Validates: Requirements 4.1, 4.14, 4.16**

  - [x]\* 2.34 Write property test that consolidated Topic_Docs name every consolidated entry
    - **Property 21: Consolidated Topic_Docs name every consolidated entry**
    - **Validates: Requirements 4.15**

  - [x]\* 2.35 Write property test for the Library_Index Phase Sub-Libraries listing
    - **Property 1: Phase Sub-Libraries listing reflects on-disk state**
    - **Validates: Requirements 1.4**

  - [x]\* 2.36 Write property test for Phase-1 non-interference under sibling addition
    - **Property 2: Phase-1 non-interference under sibling addition**
    - **Validates: Requirements 1.6**

  - [x]\* 2.37 Write property test for the External Sources app-README listing
    - **Property 3: External Sources reflects every present app README**
    - **Validates: Requirements 1.7**

  - [x]\* 2.38 Write property test for the no-50-word-duplication rule
    - **Property 4: No 50-word duplication from upstream sources**
    - **Validates: Requirements 1.8**

  - [x]\* 2.39 Write property test for the Phase_1_Index introduction word count and reference
    - **Property 5: Phase_1_Index introduction word count and reference**
    - **Validates: Requirements 2.3**

  - [x]\* 2.40 Write property test for the Phase_1_Index thematic-section sequence
    - **Property 6: Phase_1_Index thematic-section sequence equality**
    - **Validates: Requirements 2.4**

  - [x]\* 2.41 Write property test that every Topic_Doc is listed exactly once
    - **Property 7: Every Topic_Doc listed exactly once across thematic sections**
    - **Validates: Requirements 2.5**

  - [x]\* 2.42 Write property test for Topic_Doc entry markup integrity
    - **Property 8: Topic_Doc entry markup integrity in the Phase_1_Index**
    - **Validates: Requirements 2.6, 2.7**

  - [x]\* 2.43 Write property test for the Recommended reading order endpoints
    - **Property 9: Recommended reading order is exhaustive with foundation-first and hosting-last endpoints**
    - **Validates: Requirements 2.8**

  - [x]\* 2.44 Write property test for the Library_Index Non-goals minimum bullets
    - **Property 34: Library_Index Non-goals has at least four bullets**
    - **Validates: Requirements 10.1**

  - [x]\* 2.45 Write property test that the Phase_1_Sub_Library does not over-discuss future phases
    - **Property 35: Phase_1_Sub_Library does not over-discuss future phases**
    - **Validates: Requirements 10.6**

  - [x] 2.46 Implement the CLI entry point and reporter
    - Argparse CLI with `--root <repo-root>` (default: discover by walking up from CWD until a `pnpm-workspace.yaml` is found) and `--format {text,json}` (default `text`).
    - Read-only behavior; never mutates the repository.
    - Sort findings by `(file, line, rule_id)` for stable output across runs.
    - Exit code: `0` when findings is empty, `1` when any finding exists, `2` on walk failures (unreadable file, missing repo root). Internal exceptions during a per-rule check are caught and converted into `LDC999` findings so one buggy rule cannot mask others.
    - Add a top-of-file module docstring matching the style of `tools/check_env_drift.py`.
    - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2_

- [x] 3. Checkpoint — validator is complete and clean against the empty library
  - Run `python tools/learning_docs_check.py` against the current state. Confirm the only findings are the expected `LDC016` rows for unfilled `Topic coverage` filenames; confirm `LDC003`, `LDC004`–`LDC008` produce no findings on the Library_Index, Conventions_Doc, and Phase_1_Index. Run `pytest tools/tests/`. Ensure all tests pass, ask the user if questions arise.

- [x] 4. Author the "Foundation and tooling" Topic_Docs
  - For every sub-task below, follow the canonical Doc_Template (H1 + the seven required H2s, in order) defined in `CONVENTIONS.md`. Each Topic_Doc references at least one Implementation_File from the existing repository, includes one verbatim sourced fenced block with a `Source:` citation, declares prerequisite Topic_Docs in the Introduction (or "no prerequisites"), lists ≥3 labelled pitfalls, and ends with 1–10 https External reading links. Each sub-task creates exactly one new file under `docs/learning/phase-1/`; all updates to the Phase_1_Index (`docs/learning/phase-1/README.md`) are deferred to Section 17 to avoid concurrent edits to the same file. Run `python tools/learning_docs_check.py` after each sub-task and resolve every finding tied to the new Topic_Doc before moving on (`LDC017` will report the not-yet-listed Topic_Doc until Section 17 lands; that finding is expected during this batch).
  - [x] 4.1 Author `monorepo-layout.md`
    - Cover the monorepo concept and the apps-vs-packages split, anchoring to `pnpm-workspace.yaml`, `package.json`, and the `apps/`, `packages/`, `ml/`, `infra/` top-level directories.
    - _Requirements: 4.1, 4.2_

  - [x] 4.2 Author `pnpm-and-workspaces.md`
    - Cover pnpm and pnpm workspaces, anchoring to `pnpm-workspace.yaml` and the root `package.json`.
    - _Requirements: 4.1, 4.2_

  - [x] 4.3 Author `uv-python-package-manager.md`
    - Cover uv as a Python package manager, anchoring to `apps/api/pyproject.toml` and a representative `uv.lock`.
    - _Requirements: 4.1, 4.2_

  - [x] 4.4 Author `language-version-pinning.md`
    - Cover Node.js version pinning via `.nvmrc` and Python pinning via `.python-version`, anchoring to both files at repo root.
    - _Requirements: 4.1, 4.2_

  - [x] 4.5 Author `root-package-and-tsconfig.md`
    - Cover the root `package.json` and `tsconfig.base.json`, anchoring to both files.
    - _Requirements: 4.1, 4.2_

  - [x] 4.6 Author `editorconfig.md`
    - Cover `.editorconfig`, anchoring to that file.
    - _Requirements: 4.1, 4.2_

  - [x] 4.7 Author `lockfiles-and-frozen-installs.md`
    - Cover lockfiles (`pnpm-lock.yaml`, `uv.lock`) and the meaning of frozen-lockfile installs, anchoring to both files and to the relevant CI step in `.github/workflows/ci.yml`.
    - _Requirements: 4.1, 4.2_

  - [x] 4.8 Author `env-files-and-drift-detection.md`
    - Cover `.env`, `.env.example`, and the env-drift detection script, anchoring to `.env.example` and `tools/check_env_drift.py`.
    - _Requirements: 4.1, 4.2_

  - [x] 4.9 Author `pre-commit-hooks.md`
    - Cover pre-commit as a framework plus each hook used (gitleaks, ruff format, ruff check --fix, prettier, file-hygiene checks), anchoring to `.pre-commit-config.yaml`.
    - _Requirements: 4.1, 4.2_

  - [x] 4.10 Author `corepack-and-packagemanager-pin.md`
    - Cover the corepack pin that activates the repository's declared pnpm version from the root `packageManager` field, anchoring to the `packageManager` field in the root `package.json`.
    - _Requirements: 4.1, 4.2_

  - [x] 4.11 Author `nextjs-standalone-build.md`
    - Cover the Next.js `output: "standalone"` build mode and the self-contained server bundle it emits for containerized deploys, anchoring to the `output: "standalone"` setting in `apps/web/next.config.*`.
    - _Requirements: 4.1, 4.2_

- [x] 5. Author the "Frontend" Topic_Docs
  - Same authoring contract as Section 4. Anchor each Topic_Doc to files under `apps/web/`.
  - [x] 5.1 Author `nextjs-app-router-and-rsc.md`
    - Cover the Next.js App Router and the difference between Server Components and Client Components.
    - _Requirements: 4.1, 4.3_

  - [x] 5.2 Author `typescript-strict-mode.md`
    - Cover TypeScript strict mode and the specific compiler options enabled, anchoring to `tsconfig.base.json` and any per-app `tsconfig.json`.
    - _Requirements: 4.1, 4.3_

  - [x] 5.3 Author `tailwind-v4-and-theme-tokens.md`
    - Cover Tailwind CSS v4 and the `@theme inline` token strategy, anchoring to the global stylesheet under `apps/web/src/styles/`.
    - _Requirements: 4.1, 4.3_

  - [x] 5.4 Author `shadcn-ui-as-copy-in-primitives.md`
    - Cover shadcn/ui as a copy-in primitive library, anchoring to a primitive copied into `apps/web/src/components/ui/`.
    - _Requirements: 4.1, 4.3_

  - [x] 5.5 Author `geist-fonts-via-next-font.md`
    - Cover Geist Sans and Geist Mono via `next/font`, anchoring to the `apps/web/src/app/layout.tsx` (or equivalent) font-loading code.
    - _Requirements: 4.1, 4.3_

  - [x] 5.6 Author `framer-motion-and-reduced-motion.md`
    - Cover Framer Motion and the reduced-motion accessibility pattern, anchoring to a `useReducedMotion` (or equivalent) usage under `apps/web/src/`.
    - _Requirements: 4.1, 4.3_

  - [x] 5.7 Author `next-themes-and-system-default.md`
    - Cover `next-themes` and the system-default theme.
    - _Requirements: 4.1, 4.3_

  - [x] 5.8 Author `nextjs-proxy-security-headers.md`
    - Cover the security-headers proxy, including the Next.js 16 file-convention rename from `middleware.ts` to `proxy.ts`. Anchor to `apps/web/src/proxy.ts` (or `middleware.ts` if the rename has not yet landed) and clearly document which name is current.
    - _Requirements: 4.1, 4.3_

  - [x] 5.9 Author `wcag-aa-color-contrast.md`
    - Cover WCAG AA color contrast in practice, anchoring to the design-token color values in the global stylesheet.
    - _Requirements: 4.1, 4.3_

- [x] 6. Author the "Backend" Topic_Docs
  - Same authoring contract. Anchor each Topic_Doc to files under `apps/api/`.
  - [x] 6.1 Author `fastapi-application-factory.md`
    - Cover FastAPI as an async ASGI framework and the application-factory pattern, anchoring to `apps/api/src/matchlayer_api/main.py`.
    - _Requirements: 4.1, 4.4_

  - [x] 6.2 Author `pydantic-and-pydantic-settings.md`
    - Cover Pydantic v2 and `pydantic-settings`, anchoring to `apps/api/src/matchlayer_api/config.py`.
    - _Requirements: 4.1, 4.4_

  - [x] 6.3 Author `async-python-and-asyncio.md`
    - Cover async Python and the asyncio model for the Reader.
    - _Requirements: 4.1, 4.4_

  - [x] 6.4 Author `sqlalchemy-async-and-session-dependency.md`
    - Cover SQLAlchemy 2.x async engine, async session factory, and the per-request session dependency, anchoring to `apps/api/src/matchlayer_api/db/`.
    - _Requirements: 4.1, 4.4_

  - [x] 6.5 Author `connection-pooling-and-pre-ping.md`
    - Cover connection pooling and the role of `pool_pre_ping`. May be consolidated into 6.4 per Req 4.15; if consolidated, name both coverage entries verbatim in the Topic_Doc's `Introduction` and update the `Topic coverage` rows to share the same filename.
    - _Requirements: 4.1, 4.4, 4.15_

  - [x] 6.6 Author `alembic-migrations-and-empty-baseline.md`
    - Cover Alembic migrations and the empty baseline strategy, anchoring to `apps/api/alembic/`.
    - _Requirements: 4.1, 4.4_

  - [x] 6.7 Author `structlog-and-json-logging.md`
    - Cover `structlog` and structured JSON logging, anchoring to the logging configuration under `apps/api/src/matchlayer_api/core/`.
    - _Requirements: 4.1, 4.4_

  - [x] 6.8 Author `request-id-middleware.md`
    - Cover the request-id ASGI middleware and the `X-Request-Id` header contract, anchoring to the middleware module under `apps/api/src/matchlayer_api/core/`.
    - _Requirements: 4.1, 4.4_

  - [x] 6.9 Author `rfc-7807-error-envelope.md`
    - Cover the RFC 7807 error envelope, anchoring to the error-handler module under `apps/api/src/matchlayer_api/core/`.
    - _Requirements: 4.1, 4.4_

  - [x] 6.10 Author `openapi-dump-cli.md`
    - Cover the OpenAPI dump CLI and how FastAPI generates an OpenAPI document, anchoring to `apps/api/src/matchlayer_api/tools/dump_openapi.py`.
    - _Requirements: 4.1, 4.4_

- [x] 7. Checkpoint — backend and frontend Topic_Docs are complete
  - Run `python tools/learning_docs_check.py` over the whole library and `pytest tools/tests/`. Confirm the validator reports no findings except advisory `LDC012`/`LDC013`/`just`-warning entries (each must be reviewed and either resolved or documented in the PR description). Ensure all tests pass, ask the user if questions arise.

- [x] 8. Author the "Security" Topic_Docs
  - Same authoring contract. Most Topic_Docs anchor to a mix of `apps/web/`, `apps/api/`, `.github/workflows/`, and the security-related files at repo root.
  - [x] 8.1 Author `security-headers-explained.md`
    - Cover the security headers set by the Next.js proxy and what each one defends against (CSP, HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy, Permissions-Policy), anchoring to the proxy file under `apps/web/src/`.
    - _Requirements: 4.1, 4.5_

  - [x] 8.2 Author `cors-allowlists.md`
    - Cover CORS allowlists and why wildcard CORS is unsafe on authenticated endpoints, anchoring to the CORS configuration under `apps/api/src/matchlayer_api/`.
    - _Requirements: 4.1, 4.5_

  - [x] 8.3 Author `structured-logging-as-pii-defense.md`
    - Cover structured logging as a PII-defense tool and the redaction processor, anchoring to the structlog redaction module under `apps/api/src/matchlayer_api/core/`.
    - _Requirements: 4.1, 4.5_

  - [x] 8.4 Author `secrets-management.md`
    - Cover secrets management, gitleaks, GitHub Secret Scanning, and `.env` discipline, anchoring to `.pre-commit-config.yaml` (gitleaks hook), `.env.example`, and any Dependabot/secret-scanning config files.
    - _Requirements: 4.1, 4.5_

  - [x] 8.5 Author `dependency-and-supply-chain-scanning.md`
    - Cover dependency and supply-chain scanning (`pip-audit`, `pnpm audit --prod`, CodeQL, Dependabot security updates), anchoring to `.github/workflows/ci.yml` and `.github/dependabot.yml`.
    - _Requirements: 4.1, 4.5_

  - [x] 8.6 Author `threat-model-categories.md`
    - Cover the threat-model categories listed in the security steering doc. Do not duplicate prose from `.kiro/steering/security.md`; link to it instead and re-explain each category at Reader-level depth.
    - _Requirements: 4.1, 4.5, 1.8_

  - [x] 8.7 Author `pii-non-indexing.md`
    - Cover the non-indexing of PII surfaces as a privacy control (defense in depth, per `seo.md` and ADR 0006): the authenticated `(app)` route-group `robots: { index: false, follow: false }` metadata export inherited by nested routes, the `X-Robots-Tag: noindex, nofollow` response header set by the API no-index middleware on every `/api/v1/*` response, and the exclusion of PII-bearing routes from `robots.txt` and `sitemap.xml`. Anchor to the `(app)` route-group layout, the API no-index middleware under `apps/api/src/matchlayer_api/`, and `apps/web/src/app/robots.ts` and `apps/web/src/app/sitemap.ts`.
    - _Requirements: 4.1, 4.5_

- [x] 9. Author the "Database and storage" Topic_Docs
  - Same authoring contract. Anchor to `docker-compose.yml` and any DB initialization or seed scripts.
  - [x] 9.1 Author `postgresql-fundamentals.md`
    - Cover PostgreSQL 16 fundamentals for the Reader (relational model, schemas, transactions, indexes), anchoring to the Postgres service definition in `docker-compose.yml`.
    - _Requirements: 4.1, 4.7_

  - [x] 9.2 Author `postgres-vs-minio.md`
    - Cover the difference between Postgres and MinIO and why Phase 1 uses both, anchoring to both service definitions in `docker-compose.yml`.
    - _Requirements: 4.1, 4.7_

  - [x] 9.3 Author `redis-fundamentals.md`
    - Cover Redis fundamentals and why Phase 1 stands it up even though it is unused until Phase 4. Limit any Phase ≥ 2 mention to at most two sentences per Property 35.
    - _Requirements: 4.1, 4.7, 10.6_

  - [x] 9.4 Author `named-docker-volumes.md`
    - Cover named Docker volumes and data persistence across `docker compose down`, anchoring to the `volumes:` section in `docker-compose.yml`.
    - _Requirements: 4.1, 4.7_

  - [x] 9.5 Author `pgvector-and-the-phase-2-boundary.md`
    - Cover the future addition of pgvector in Phase 2 and why Phase 1 stops short of it. Constrain Phase 2 mentions to at most two sentences and avoid implementation guidance for Phase 2 (Property 35; Req 10.6).
    - _Requirements: 4.1, 4.7, 10.6_

- [x] 10. Author the "Containerization" Topic_Docs
  - Same authoring contract. Anchor to `docker-compose.yml`, `infra/docker/web.Dockerfile`, and `infra/docker/api.Dockerfile`.
  - [x] 10.1 Author `containers-vs-vms.md`
    - Cover what a container is and how it differs from a virtual machine.
    - _Requirements: 4.1, 4.9_

  - [x] 10.2 Author `docker-images-layers-and-cache.md`
    - Cover Docker images, layers, and the build cache, anchoring to `infra/docker/api.Dockerfile`.
    - _Requirements: 4.1, 4.9_

  - [x] 10.3 Author `dockerfiles-and-multi-stage-builds.md`
    - Cover Dockerfiles and multi-stage builds, anchoring to `infra/docker/web.Dockerfile` and `infra/docker/api.Dockerfile`.
    - _Requirements: 4.1, 4.9_

  - [x] 10.4 Author `docker-compose-and-healthchecks.md`
    - Cover `docker compose` as a multi-service local-development tool, healthchecks, and the `--wait` flag, anchoring to `docker-compose.yml`.
    - _Requirements: 4.1, 4.9_

  - [x] 10.5 Author `production-dockerfiles.md`
    - Cover the production Dockerfiles in `infra/docker/` and what each instruction does, anchoring to both Dockerfiles. May consolidate with 10.3 or 10.4 per Req 4.15.
    - _Requirements: 4.1, 4.9, 4.15_

  - [x] 10.6 Author `distroless-and-runtime-hardening.md`
    - Cover distroless base images, non-root users with high UIDs, the `--read-only` runtime contract, and why these matter for security, anchoring to the production Dockerfiles.
    - _Requirements: 4.1, 4.9_

  - [x] 10.7 Author `image-digest-pinning.md`
    - Cover image digest pinning, anchoring to the `FROM ...@sha256:...` lines in the production Dockerfiles.
    - _Requirements: 4.1, 4.9_

- [x] 11. Author the "Contracts and codegen" Topic_Docs
  - Same authoring contract. Anchor to the OpenAPI dump CLI, the codegen orchestrator script under `tools/` or `scripts/`, and the generated outputs under `packages/shared-types/`.
  - [x] 11.1 Author `openapi-from-fastapi.md`
    - Cover what OpenAPI is and how FastAPI generates it, anchoring to `apps/api/src/matchlayer_api/tools/dump_openapi.py`.
    - _Requirements: 4.1, 4.10_

  - [x] 11.2 Author `codegen-orchestrator-and-execa.md`
    - Cover the codegen orchestrator script and the role of `execa`, anchoring to the orchestrator script and its `package.json` script entry.
    - _Requirements: 4.1, 4.10_

  - [x] 11.3 Author `openapi-typescript-codegen.md`
    - Cover `openapi-typescript` and what it produces, anchoring to the generated TypeScript types under `packages/shared-types/`.
    - _Requirements: 4.1, 4.10_

  - [x] 11.4 Author `openapi-zod-client-codegen.md`
    - Cover `openapi-zod-client` and what it produces, anchoring to the generated Zod schemas under `packages/shared-types/`.
    - _Requirements: 4.1, 4.10_

  - [x] 11.5 Author `shared-types-curated-reexports.md`
    - Cover the curated `index.ts` re-export pattern, anchoring to `packages/shared-types/src/index.ts`.
    - _Requirements: 4.1, 4.10_

  - [x] 11.6 Author `openapi-drift-check.md`
    - Cover the OpenAPI drift check in CI and why it exists, anchoring to the relevant job in `.github/workflows/ci.yml`.
    - _Requirements: 4.1, 4.10_

- [x] 12. Author the "Hosting and deploy" Topic_Docs
  - Same authoring contract. Anchor to `.github/workflows/ci.yml`, `.github/dependabot.yml`, and any Vercel/Fly.io configuration files committed to the repo.
  - [x] 12.1 Author `github-actions-workflow-structure.md`
    - Cover GitHub Actions workflow structure (jobs, steps, triggers, concurrency, caching), anchoring to `.github/workflows/ci.yml`.
    - _Requirements: 4.1, 4.11_

  - [x] 12.2 Author `phase-1-ci-jobs.md`
    - Cover the five CI jobs in this repo (`backend`, `frontend`, `shared-types`, `security`, `openapi-drift`) and what each verifies, anchoring to the corresponding job blocks in `.github/workflows/ci.yml`.
    - _Requirements: 4.1, 4.11_

  - [x] 12.3 Author `dependabot-configuration.md`
    - Cover Dependabot configuration, anchoring to `.github/dependabot.yml`.
    - _Requirements: 4.1, 4.11_

  - [x] 12.4 Author `branch-protection-and-required-checks.md`
    - Cover branch protection rules and the required-checks aggregator job pattern, anchoring to the aggregator job in `.github/workflows/ci.yml`.
    - _Requirements: 4.1, 4.11_

  - [x] 12.5 Author `vercel-hobby-tier-hosting.md`
    - Cover Vercel hobby tier as the Phase 1 frontend host, anchoring to any Vercel config (e.g., `vercel.json`) committed to the repo or to `apps/web/`'s deploy notes.
    - _Requirements: 4.1, 4.11_

  - [x] 12.6 Author `flyio-backend-hosting.md`
    - Cover Fly.io as the Phase 1 backend host, anchoring to the `fly.toml` file (if committed) under `apps/api/` or to the deploy script.
    - _Requirements: 4.1, 4.11_

  - [x] 12.7 Author `aws-s3-in-phase-1.md`
    - Cover AWS S3 as the Phase 1 file-storage backend.
    - _Requirements: 4.1, 4.11_

  - [x] 12.8 Author `aws-migration-path-preservation.md`
    - Cover how Phase 1 hosting choices preserve the Phase 6 AWS migration path. Limit Phase ≥ 2 mentions to at most two sentences and avoid implementation guidance for future phases (Property 35; Req 10.6).
    - _Requirements: 4.1, 4.11, 10.6_

- [x] 13. Author the "Authentication and accounts" Topic_Docs
  - Same authoring contract as Section 4. Sourced from the `phase-1-auth` spec. Anchor each Topic_Doc to files under `apps/api/src/matchlayer_api/api/auth/`, the auth services and `core/` security helpers under `apps/api/src/matchlayer_api/`, and the auth UI under `apps/web/src/`.
  - [x] 13.1 Author `jwt-and-pyjwt.md`
    - Cover JSON Web Tokens (JWT) and the PyJWT library, the difference between short-lived access tokens and longer-lived refresh tokens, and the explicit JWT algorithm allowlist on verification and why Phase 1 signs with HS256 rather than RS256/JWKS. Anchor to the token-encode/decode module under `apps/api/src/matchlayer_api/` auth services or `core/`.
    - _Requirements: 4.1, 4.6_

  - [x] 13.2 Author `password-hashing-argon2id.md`
    - Cover password hashing with Argon2id via `argon2-cffi` and the top-1000 common-password blocklist, anchoring to the password-hashing helper and blocklist under `apps/api/src/matchlayer_api/`.
    - _Requirements: 4.1, 4.6_

  - [x] 13.3 Author `refresh-token-rotation.md`
    - Cover stateful refresh-token rotation and family-based reuse detection (revoking the whole token family when a revoked token is replayed), anchoring to the refresh-token service and model under `apps/api/src/matchlayer_api/`.
    - _Requirements: 4.1, 4.6_

  - [x] 13.4 Author `csrf-and-secure-cookies.md`
    - Cover the double-submit-cookie CSRF pattern and the `HttpOnly`, `Secure`, and `SameSite` cookie attributes, anchoring to the cookie/CSRF handling under `apps/api/src/matchlayer_api/api/auth/` and the matching frontend handling under `apps/web/src/`.
    - _Requirements: 4.1, 4.6_

  - [x] 13.5 Author `rate-limiting-and-account-lockout.md`
    - Cover Redis-backed sliding-window rate limiting and the account-lockout policy after repeated failed logins, anchoring to the rate-limiter dependency/service under `apps/api/src/matchlayer_api/`.
    - _Requirements: 4.1, 4.6_

  - [x] 13.6 Author `append-only-audit-log.md`
    - Cover the append-only `audit_events` log and why append-only (no UPDATE, no DELETE from application code) matters, anchoring to the audit-event model and writer under `apps/api/src/matchlayer_api/`.
    - _Requirements: 4.1, 4.6_

  - [x] 13.7 Author `password-reset-tokens.md`
    - Cover password-reset tokens that are hashed at rest, single-use, and TTL-bounded, plus the dev-only reset-link surface, anchoring to the password-reset service under `apps/api/src/matchlayer_api/api/auth/`.
    - _Requirements: 4.1, 4.6_

  - [x] 13.8 Author `tanstack-query-and-useauth.md`
    - Cover TanStack Query and the `useAuth` server-state hook, anchoring to the `useAuth` hook and query-client setup under `apps/web/src/`.
    - _Requirements: 4.1, 4.6_

  - [x] 13.9 Author `authenticated-route-group-shell.md`
    - Cover the authenticated route-group shell `(app)` and the redirect-on-unauthenticated pattern, anchoring to the `(app)` route-group layout under `apps/web/src/app/`.
    - _Requirements: 4.1, 4.6_

  - [x] 13.10 Author `no-account-enumeration.md`
    - Cover the no-account-enumeration defense whereby login verifies a dummy Argon2id hash for unknown accounts so failed logins equalize response time, and "user not found" and "wrong password" return an identical generic error. Anchor to the login service under `apps/api/src/matchlayer_api/api/auth/`.
    - _Requirements: 4.1, 4.6_

- [x] 14. Author the "Matching and scoring" Topic_Docs
  - Same authoring contract as Section 4. Sourced from the `phase-1-matching` spec. Anchor each Topic_Doc to the resumes/matches API and services under `apps/api/src/matchlayer_api/`, the lexicon artifact `ml/lexicon/skill_lexicon.v1.json`, the build pipeline `ml/pipelines/build_skill_lexicon.py`, the drift check `tools/check_lexicon_drift.py`, and the matching UI under `apps/web/src/`.
  - [x] 14.1 Author `ats-scoring-overview.md`
    - Cover what an Applicant Tracking System (ATS) match score is in Phase 1, the deterministic non-LLM scoring approach, and why Phase 1 takes it ("infrastructure before intelligence" and the $20/month cost ceiling), anchoring to the scoring service under `apps/api/src/matchlayer_api/`.
    - _Requirements: 4.1, 4.8_

  - [x] 14.2 Author `tf-idf-and-cosine-similarity.md`
    - Cover Term Frequency–Inverse Document Frequency (TF-IDF) and cosine similarity via scikit-learn, anchoring to the TF-IDF scorer under `ml/` or its thin client under `apps/api/src/matchlayer_api/ml/`.
    - _Requirements: 4.1, 4.8_

  - [x] 14.3 Author `skill-lexicon-and-keyword-overlap.md`
    - Cover keyword and skill overlap analysis against the committed, versioned Skill_Lexicon (matched versus missing terms), anchoring to `ml/lexicon/skill_lexicon.v1.json` and the overlap analyzer under `apps/api/src/matchlayer_api/`.
    - _Requirements: 4.1, 4.8_

  - [x] 14.4 Author `rule-based-suggestions.md`
    - Cover rule-based suggestion generation derived from missing terms with no LLM, anchoring to the suggestion generator under `apps/api/src/matchlayer_api/`.
    - _Requirements: 4.1, 4.8_

  - [x] 14.5 Author `file-upload-safety.md`
    - Cover file-upload safety (server-side magic-byte MIME validation rather than trusting the `Content-Type` header, hard size limits, UUID object keys, and the original filename retained as display-only), anchoring to the upload handler under `apps/api/src/matchlayer_api/api/resumes/`.
    - _Requirements: 4.1, 4.8_

  - [x] 14.6 Author `pdf-docx-text-extraction.md`
    - Cover bounded server-side PDF and DOCX text extraction (resource bounds and the one-way transformation, not a reversible parser), anchoring to the extraction service under `apps/api/src/matchlayer_api/`.
    - _Requirements: 4.1, 4.8_

  - [x] 14.7 Author `s3-minio-storage-abstraction.md`
    - Cover the S3/MinIO storage abstraction presenting the same interface locally and in production, anchoring to the storage-client abstraction under `apps/api/src/matchlayer_api/`.
    - _Requirements: 4.1, 4.8_

  - [x] 14.8 Author `usage-quotas-cost-as-dos.md`
    - Cover per-user daily upload and scoring quotas as a cost-as-DoS defense, anchoring to the quota enforcement under `apps/api/src/matchlayer_api/`.
    - _Requirements: 4.1, 4.8_

  - [x] 14.9 Author `ml-vs-api-separation-and-scorer-version.md`
    - Cover the `ml/`-versus-`apps/api/` code separation for the scorer and lexicon and the Scorer_Version reproducibility identifier, anchoring to the `ml/` scorer module and the thin client under `apps/api/src/matchlayer_api/ml/`.
    - _Requirements: 4.1, 4.8_

  - [x] 14.10 Author `zod-runtime-validation.md`
    - Cover Zod runtime validation on the frontend (generated from OpenAPI) for the new endpoints, anchoring to the generated Zod schemas under `packages/shared-types/` and their use in the matching UI under `apps/web/src/`.
    - _Requirements: 4.1, 4.8_

  - [x] 14.11 Author `skill-lexicon-build-pipeline.md`
    - Cover the skill-lexicon build pipeline that regenerates the committed lexicon artifact from curated source data, anchoring to `ml/pipelines/build_skill_lexicon.py` and `ml/lexicon/skill_lexicon.v1.json`.
    - _Requirements: 4.1, 4.8_

  - [x] 14.12 Author `lexicon-drift-check.md`
    - Cover the lexicon drift check that fails CI when the committed artifact and its API package copy diverge or go stale, anchoring to `tools/check_lexicon_drift.py` and the relevant job in `.github/workflows/ci.yml`.
    - _Requirements: 4.1, 4.8_

  - [x] 14.13 Author `zip-bomb-defense.md`
    - Cover the zip-bomb / decompression-bomb defense applied during file handling (bounding decompressed size so a small malicious upload cannot exhaust memory or disk), anchoring to the decompression-bound logic in the extraction/upload path under `apps/api/src/matchlayer_api/`.
    - _Requirements: 4.1, 4.8_

- [x] 15. Author the "API and data conventions" Topic_Docs
  - Same authoring contract as Section 4. Cross-cutting and sourced from the `phase-1-auth` and `phase-1-matching` specs. Anchor each Topic_Doc to the SQLAlchemy model base under `apps/api/src/matchlayer_api/db/`, the pagination helpers, the idempotency handling, and the FastAPI router base under `apps/api/src/matchlayer_api/`.
  - [x] 15.1 Author `uuidv7-identifiers.md`
    - Cover UUIDv7 (version 7 of the Universally Unique Identifier standard) time-ordered identifiers exposed as opaque strings and why database sequence integers are never exposed, anchoring to the UUIDv7 column default in the SQLAlchemy model base under `apps/api/src/matchlayer_api/db/`.
    - _Requirements: 4.1, 4.12_

  - [x] 15.2 Author `soft-delete-and-deleted-at.md`
    - Cover soft-delete via a `deleted_at` timestamp and why user data is not hard-deleted without an explicit request, anchoring to the soft-delete mixin/base under `apps/api/src/matchlayer_api/db/`.
    - _Requirements: 4.1, 4.12_

  - [x] 15.3 Author `cursor-pagination.md`
    - Cover cursor-based pagination (`?limit=&cursor=`) and why offset pagination is avoided, anchoring to the pagination helper under `apps/api/src/matchlayer_api/`.
    - _Requirements: 4.1, 4.12_

  - [x] 15.4 Author `idempotency-keys.md`
    - Cover idempotency keys supplied via the `Idempotency-Key` header on mutating endpoints (uploads, password reset, webhook handlers) and the 24-hour key-persistence window, anchoring to the idempotency handling under `apps/api/src/matchlayer_api/`.
    - _Requirements: 4.1, 4.12_

  - [x] 15.5 Author `api-versioning-and-resource-naming.md`
    - Cover the versioned `/api/v1` base path with the plural-resource path-naming convention and the ISO 8601 UTC timestamp conventions with the `Z` suffix (`created_at`, `updated_at`), anchoring to the FastAPI router base and the timestamp columns under `apps/api/src/matchlayer_api/`.
    - _Requirements: 4.1, 4.12_

- [x] 16. Author the "Testing and quality" Topic_Docs
  - Same authoring contract as Section 4. Sourced from all three Phase_1_Implementation_Specs. Anchor each Topic_Doc to the backend test suite under `apps/api/tests/`, the frontend test setup under `apps/web/`, the Playwright configuration, and the `conftest`/Hypothesis settings.
  - [x] 16.1 Author `pytest-and-httpx-backend-testing.md`
    - Cover pytest, pytest-asyncio, and httpx for backend unit and integration tests, anchoring to the backend test configuration and a representative test under `apps/api/tests/`.
    - _Requirements: 4.1, 4.13_

  - [x] 16.2 Author `integration-testing-with-real-postgres.md`
    - Cover the real-Postgres-in-Docker integration testing approach and why integration tests run against a real database rather than mocks, anchoring to the `conftest` database fixtures under `apps/api/tests/`.
    - _Requirements: 4.1, 4.13_

  - [x] 16.3 Author `vitest-and-testing-library.md`
    - Cover Vitest and Testing Library for frontend component tests, anchoring to the Vitest config and a representative component test under `apps/web/`.
    - _Requirements: 4.1, 4.13_

  - [x] 16.4 Author `playwright-e2e-testing.md`
    - Cover Playwright for end-to-end (E2E) tests, anchoring to the Playwright configuration and a representative E2E spec under `apps/web/`.
    - _Requirements: 4.1, 4.13_

  - [x] 16.5 Author `property-based-testing-with-hypothesis.md`
    - Cover Hypothesis property-based testing and what a "property" is for the Reader (a general assertion checked against many generated inputs rather than a single hand-written example), anchoring to the Hypothesis settings and a representative property test under `apps/api/tests/` or `tools/tests/`.
    - _Requirements: 4.1, 4.13_

  - [x] 16.6 Author `test-taxonomy-and-layers.md`
    - Cover the test taxonomy of layers used across Phase 1 (unit, integration, property, smoke, E2E, accessibility, and timing tests) and what each layer verifies, anchoring to the test directory layout under `apps/api/tests/` and `apps/web/`.
    - _Requirements: 4.1, 4.13_

  - [x] 16.7 Author `axe-core-accessibility-testing.md`
    - Cover axe-core accessibility tests, anchoring to the axe-core test setup under `apps/web/`.
    - _Requirements: 4.1, 4.13_

  - [x] 16.8 Author `import-boundary-tests.md`
    - Cover import-boundary tests that enforce the apps-vs-packages and `ml/`-versus-`apps/api/` separation, anchoring to the import-boundary test under `apps/api/tests/` (or the equivalent guard under `tools/tests/`).
    - _Requirements: 4.1, 4.13_

  - [x] 16.9 Author `timing-equalization-tests.md`
    - Cover the timing-category tests that verify the no-account-enumeration response-time equalization, anchoring to the timing test under `apps/api/tests/`.
    - _Requirements: 4.1, 4.13_

- [x] 17. Checkpoint — authentication, matching, API-conventions, and testing Topic_Docs are complete
  - Run `python tools/learning_docs_check.py` over the whole library and `pytest tools/tests/`. Confirm the validator reports no findings except advisory `LDC012`/`LDC013`/`just`-warning entries and the expected `LDC017` not-yet-listed findings (resolved once Section 18 lands). Ensure all tests pass, ask the user if questions arise.

- [x] 18. Finalize the Phase_1_Index and library cross-links
  - [x] 18.1 Complete the `Topic coverage` table
    - Fill the `Topic_Doc filename` cell for every coverage-list row, using the design's mapping as the baseline. Where consolidation has been applied (e.g., 6.4 absorbed 6.5), the consolidated rows share the same filename and the absorbing Topic_Doc names every consolidated entry verbatim in its `Introduction` (Req 4.15).
    - _Requirements: 4.1, 4.14, 4.15, 4.16_

  - [x] 18.2 Populate the twelve thematic sections in the Phase_1_Index
    - Under each thematic-section H2, add one Markdown hyperlink per Topic_Doc (link target = filename relative to `docs/learning/phase-1/`, link text = Topic_Doc's H1 title), each followed by an 8–30 word single-sentence summary ending with a period. Every Topic_Doc appears in exactly one thematic section.
    - The twelve thematic-section H2s appear in this exact order (Req 2.4 canonical order): `Foundation and tooling`, `Frontend`, `Backend`, `API and data conventions`, `Security`, `Authentication and accounts`, `Database and storage`, `Matching and scoring`, `Testing and quality`, `Containerization`, `Contracts and codegen`, `Hosting and deploy`.
    - _Requirements: 2.4, 2.5, 2.6, 2.7_

  - [x] 18.3 Populate `Recommended reading order`
    - Build the numbered list as the union of every Topic_Doc filename present under `docs/learning/phase-1/`. The first entry is from `Foundation and tooling`; the last entry is from `Hosting and deploy`. Order entries by walking the twelve thematic sections in their Req 2.4 canonical order (`Foundation and tooling` → `Frontend` → `Backend` → `API and data conventions` → `Security` → `Authentication and accounts` → `Database and storage` → `Matching and scoring` → `Testing and quality` → `Containerization` → `Contracts and codegen` → `Hosting and deploy`), so the list builds conceptually from foundation through application surface and hardening to deploy.
    - _Requirements: 2.8_

  - [x] 18.4 Update the Library_Index for the populated state
    - Confirm `Phase Sub-Libraries` lists `phase-1/`. Confirm `External Sources` and `Non-goals` reference every existing `apps/*/README.md`. Confirm the Non-goals link to the committed OpenAPI document still resolves (and the path matches whatever the OpenAPI dump CLI emits).
    - _Requirements: 1.4, 1.7, 10.2, 10.3, 10.4, 10.5, 10.7_

- [x] 19. Final checkpoint — full validator run on the populated library
  - Run `python tools/learning_docs_check.py --format text` against the whole library and `pytest tools/tests/`. Confirm zero blocking findings; review and resolve every advisory finding (`LDC012`, `LDC013`, and `just`-warning entries from `LDC014`). Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for a faster MVP. They are exclusively property tests and one smoke-test sub-task. Core implementation tasks (validator rules, Topic_Doc authoring, index wiring) are never marked optional.
- Each property test is its own sub-task and is annotated with both the property number from `design.md` and the requirements clauses it validates.
- Topic_Doc authoring is gated by the validator: do not advance past a thematic section while `learning_docs_check.py` reports any finding for it.
- Anchor paths cited in tasks are based on the existing repository layout and the `phase-1-foundation`, `phase-1-auth`, and `phase-1-matching` specs; if a referenced Implementation_File does not yet exist on the working branch, either author the Topic_Doc against the file once it lands or pause the sub-task. The same-PR-update rule (Req 9) governs any Implementation_File renames or removals during this work.
- Property tests rely on Hypothesis as a dev dependency under `tools/`; the runtime validator stays stdlib-only.
- The validator never makes network requests. External-link reachability over HTTP is out of scope per the requirements introduction.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4", "2.1"] },
    { "id": 2, "tasks": ["2.2"] },
    { "id": 3, "tasks": ["2.3", "2.4"] },
    { "id": 4, "tasks": ["2.5", "2.7"] },
    { "id": 5, "tasks": ["2.6", "2.9"] },
    { "id": 6, "tasks": ["2.8", "2.16"] },
    { "id": 7, "tasks": ["2.10", "2.21"] },
    { "id": 8, "tasks": ["2.11", "2.26"] },
    { "id": 9, "tasks": ["2.12", "2.32"] },
    { "id": 10, "tasks": ["2.13", "2.46"] },
    {
      "id": 11,
      "tasks": [
        "2.14",
        "4.1",
        "4.2",
        "4.3",
        "4.4",
        "4.5",
        "4.6",
        "4.7",
        "4.8",
        "4.9",
        "4.10",
        "4.11",
        "5.1",
        "5.2",
        "5.3",
        "5.4",
        "5.5",
        "5.6",
        "5.7",
        "5.8",
        "5.9",
        "6.1",
        "6.2",
        "6.3",
        "6.4",
        "6.6",
        "6.7",
        "6.8",
        "6.9",
        "6.10",
        "8.1",
        "8.2",
        "8.3",
        "8.4",
        "8.5",
        "8.6",
        "8.7",
        "9.1",
        "9.2",
        "9.3",
        "9.4",
        "9.5",
        "10.1",
        "10.2",
        "10.3",
        "10.4",
        "10.6",
        "10.7",
        "11.1",
        "11.2",
        "11.3",
        "11.4",
        "11.5",
        "11.6",
        "12.1",
        "12.2",
        "12.3",
        "12.4",
        "12.5",
        "12.6",
        "12.7",
        "12.8",
        "13.1",
        "13.2",
        "13.3",
        "13.4",
        "13.5",
        "13.6",
        "13.7",
        "13.8",
        "13.9",
        "13.10",
        "14.1",
        "14.2",
        "14.3",
        "14.4",
        "14.5",
        "14.6",
        "14.7",
        "14.8",
        "14.9",
        "14.10",
        "14.11",
        "14.12",
        "14.13",
        "15.1",
        "15.2",
        "15.3",
        "15.4",
        "15.5",
        "16.1",
        "16.2",
        "16.3",
        "16.4",
        "16.5",
        "16.6",
        "16.7",
        "16.8",
        "16.9"
      ]
    },
    { "id": 12, "tasks": ["2.15", "6.5", "10.5"] },
    { "id": 13, "tasks": ["2.17", "18.1", "18.4"] },
    { "id": 14, "tasks": ["2.18", "18.2"] },
    { "id": 15, "tasks": ["2.19", "18.3"] },
    { "id": 16, "tasks": ["2.20"] },
    { "id": 17, "tasks": ["2.22"] },
    { "id": 18, "tasks": ["2.23"] },
    { "id": 19, "tasks": ["2.24"] },
    { "id": 20, "tasks": ["2.25"] },
    { "id": 21, "tasks": ["2.27"] },
    { "id": 22, "tasks": ["2.28"] },
    { "id": 23, "tasks": ["2.29"] },
    { "id": 24, "tasks": ["2.30"] },
    { "id": 25, "tasks": ["2.31"] },
    { "id": 26, "tasks": ["2.33"] },
    { "id": 27, "tasks": ["2.34"] },
    { "id": 28, "tasks": ["2.35"] },
    { "id": 29, "tasks": ["2.36"] },
    { "id": 30, "tasks": ["2.37"] },
    { "id": 31, "tasks": ["2.38"] },
    { "id": 32, "tasks": ["2.39"] },
    { "id": 33, "tasks": ["2.40"] },
    { "id": 34, "tasks": ["2.41"] },
    { "id": 35, "tasks": ["2.42"] },
    { "id": 36, "tasks": ["2.43"] },
    { "id": 37, "tasks": ["2.44"] },
    { "id": 38, "tasks": ["2.45"] }
  ]
}
```
