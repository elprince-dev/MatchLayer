#!/usr/bin/env python3
"""Regenerate the MatchLayer Skill_Lexicon artifact (phase-1-matching).

Anchors:
    * ``.kiro/specs/phase-1-matching/requirements.md`` → Requirement 10.3,
      10.4 (the lexicon is a committed, versioned artifact whose *source of
      truth* lives under ``ml/``; any regeneration script lives under
      ``ml/pipelines/`` and is **never imported by the API at runtime**).
    * ``.kiro/specs/phase-1-matching/design.md`` → "Skill_Lexicon" and
      "Source of truth vs runtime artifact".
    * ``.kiro/steering/structure.md`` → "the API imports trained artifacts,
      not training code"; training/derivation scripts live in ``ml/pipelines/``.

What this script does
---------------------
It holds the curated Phase 1 skill data inline (no LLM, no external source —
Requirement 10's non-LLM constraint) and serializes it **deterministically**
to the canonical source artifact:

    ml/lexicon/skill_lexicon.v1.json

and then copies those exact bytes to the package-data artifact the API ships:

    apps/api/src/matchlayer_api/scoring/data/skill_lexicon.v1.json

The two files are byte-identical by construction. CI enforces that they stay
that way via ``tools/check_lexicon_drift.py`` (mirroring the ``.env`` and
OpenAPI drift gates). If you edit the curated data below, re-run this script
and commit *both* regenerated files.

This script is intentionally **stdlib-only** so it can run from any CI image
with a recent Python interpreter, before project dependencies are installed,
and so the API package never needs it on its import path.

Usage
-----
::

    # Regenerate both artifacts (the normal path after editing the data):
    python3 ml/pipelines/build_skill_lexicon.py

    # Verify the committed artifacts match what this script would emit,
    # without writing anything (exit 1 on drift):
    python3 ml/pipelines/build_skill_lexicon.py --check

Run from anywhere; paths are resolved relative to this file's location.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------

# The artifact schema version. Bump only when the *shape* of the JSON changes
# (the loader in scoring/lexicon.py reads against this). Distinct from the
# lexicon content version below.
SCHEMA_VERSION = 1

# The lexicon content version. This string flows into the Scorer_Version
# (``f"{ALGORITHM_VERSION}+lex.{lexicon_version}"`` — Requirement 10.4), so a
# change to the curated terms below MUST bump this so previously-persisted
# match_results remain attributable to the lexicon that produced them.
LEXICON_VERSION = "1.0.0"


# ---------------------------------------------------------------------------
# Paths (resolved relative to this file: ml/pipelines/build_skill_lexicon.py)
# ---------------------------------------------------------------------------

REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent
# Canonical source of truth (lives under ml/, per structure.md / Req 10.3).
SOURCE_ARTIFACT: Path = REPO_ROOT / "ml" / "lexicon" / "skill_lexicon.v1.json"
# Committed package-data copy the API ships and loads via importlib.resources.
PACKAGE_ARTIFACT: Path = (
    REPO_ROOT
    / "apps"
    / "api"
    / "src"
    / "matchlayer_api"
    / "scoring"
    / "data"
    / "skill_lexicon.v1.json"
)


# ---------------------------------------------------------------------------
# Curated skill data
# ---------------------------------------------------------------------------
#
# Each entry is (canonical, display, category, weight, aliases).
#
#   canonical — the normalized key used for matching (lowercase, no aliases).
#   display   — human-friendly label for the UI / suggestion templates.
#   category  — coarse grouping; suggestion templates key off this.
#   weight    — relative importance (0 < w <= 1). Keyword/suggestion lists are
#               ordered by descending weight, so in-demand core skills surface
#               first. Default-ish skills sit at 0.7; the most universally
#               requested at 1.0.
#   aliases   — surface forms that normalize to `canonical` (case-folded).
#
# Curated by hand from common resume/JD vocabulary. No LLM, no scraped source
# (Requirement 10: "derived only from non-LLM sources"). Keep it broad but not
# exhaustive — Phase 1 scoring also mines the JD's own high-TF-IDF terms, so
# the lexicon only needs to cover the durable, alias-prone skill vocabulary.

_RawSkill = tuple[str, str, str, float, list[str]]

_SKILLS: list[_RawSkill] = [
    # --- Programming languages -------------------------------------------
    ("python", "Python", "language", 1.0, ["py", "python3", "cpython"]),
    ("javascript", "JavaScript", "language", 1.0, ["js", "ecmascript"]),
    ("typescript", "TypeScript", "language", 0.95, ["ts"]),
    ("java", "Java", "language", 0.9, []),
    ("c#", "C#", "language", 0.85, ["csharp", "c sharp", "dotnet c#"]),
    ("c++", "C++", "language", 0.8, ["cpp", "cplusplus", "c plus plus"]),
    ("go", "Go", "language", 0.85, ["golang"]),
    ("rust", "Rust", "language", 0.8, []),
    ("ruby", "Ruby", "language", 0.7, []),
    ("php", "PHP", "language", 0.6, []),
    ("kotlin", "Kotlin", "language", 0.75, []),
    ("swift", "Swift", "language", 0.7, []),
    ("scala", "Scala", "language", 0.7, []),
    ("sql", "SQL", "language", 0.95, ["structured query language"]),
    ("bash", "Bash", "language", 0.6, ["shell", "shell scripting", "sh"]),
    # --- Frontend frameworks / libraries ---------------------------------
    ("react", "React", "framework", 0.95, ["reactjs", "react.js"]),
    ("next.js", "Next.js", "framework", 0.85, ["nextjs", "next js"]),
    ("vue", "Vue", "framework", 0.75, ["vuejs", "vue.js"]),
    ("angular", "Angular", "framework", 0.75, ["angularjs"]),
    ("svelte", "Svelte", "framework", 0.6, ["sveltekit"]),
    ("tailwind", "Tailwind CSS", "framework", 0.7, ["tailwindcss", "tailwind css"]),
    ("redux", "Redux", "library", 0.65, []),
    # --- Backend frameworks ----------------------------------------------
    ("fastapi", "FastAPI", "framework", 0.9, ["fast api"]),
    ("django", "Django", "framework", 0.85, []),
    ("flask", "Flask", "framework", 0.8, []),
    ("express", "Express", "framework", 0.75, ["expressjs", "express.js"]),
    ("spring", "Spring", "framework", 0.8, ["spring boot", "springboot"]),
    ("node.js", "Node.js", "framework", 0.9, ["node", "nodejs", "node js"]),
    ("rails", "Ruby on Rails", "framework", 0.65, ["ruby on rails", "ror"]),
    (".net", ".NET", "framework", 0.75, ["dotnet", "dot net", "asp.net", "aspnet"]),
    # --- Databases / storage ---------------------------------------------
    ("postgresql", "PostgreSQL", "database", 0.9, ["postgres", "psql", "postgre"]),
    ("mysql", "MySQL", "database", 0.8, []),
    ("mongodb", "MongoDB", "database", 0.8, ["mongo"]),
    ("redis", "Redis", "database", 0.8, []),
    ("elasticsearch", "Elasticsearch", "database", 0.7, ["elastic search", "es"]),
    ("dynamodb", "DynamoDB", "database", 0.7, ["dynamo"]),
    ("sqlite", "SQLite", "database", 0.55, []),
    ("pgvector", "pgvector", "database", 0.6, ["pg vector"]),
    # --- Cloud platforms -------------------------------------------------
    ("aws", "AWS", "cloud", 0.95, ["amazon web services"]),
    ("gcp", "Google Cloud", "cloud", 0.8, ["google cloud", "google cloud platform"]),
    ("azure", "Azure", "cloud", 0.8, ["microsoft azure"]),
    ("s3", "Amazon S3", "cloud", 0.7, ["amazon s3"]),
    ("lambda", "AWS Lambda", "cloud", 0.7, ["aws lambda"]),
    ("ecs", "Amazon ECS", "cloud", 0.6, ["amazon ecs", "fargate"]),
    # --- DevOps / infra --------------------------------------------------
    ("docker", "Docker", "devops", 0.9, ["containerization"]),
    ("kubernetes", "Kubernetes", "devops", 0.85, ["k8s"]),
    ("terraform", "Terraform", "devops", 0.75, []),
    (
        "ci/cd",
        "CI/CD",
        "devops",
        0.85,
        ["cicd", "ci cd", "continuous integration", "continuous delivery"],
    ),
    ("github actions", "GitHub Actions", "devops", 0.65, ["gha"]),
    ("jenkins", "Jenkins", "devops", 0.6, []),
    ("aws cdk", "AWS CDK", "devops", 0.55, ["cdk"]),
    ("nginx", "Nginx", "devops", 0.6, []),
    ("linux", "Linux", "devops", 0.75, ["unix"]),
    # --- Data / ML -------------------------------------------------------
    ("pandas", "pandas", "data", 0.75, []),
    ("numpy", "NumPy", "data", 0.7, ["np"]),
    ("scikit-learn", "scikit-learn", "data", 0.75, ["sklearn", "scikit learn"]),
    ("pytorch", "PyTorch", "data", 0.7, ["torch"]),
    ("tensorflow", "TensorFlow", "data", 0.7, ["tf"]),
    ("spark", "Apache Spark", "data", 0.65, ["apache spark", "pyspark"]),
    ("airflow", "Apache Airflow", "data", 0.6, ["apache airflow"]),
    ("machine learning", "Machine Learning", "data", 0.85, ["ml"]),
    ("nlp", "NLP", "data", 0.65, ["natural language processing"]),
    # --- Tools / protocols -----------------------------------------------
    ("git", "Git", "tool", 0.85, ["version control"]),
    ("graphql", "GraphQL", "tool", 0.7, ["graph ql"]),
    ("rest", "REST", "tool", 0.85, ["rest api", "restful", "restful api"]),
    ("grpc", "gRPC", "tool", 0.6, []),
    ("kafka", "Apache Kafka", "tool", 0.7, ["apache kafka"]),
    ("rabbitmq", "RabbitMQ", "tool", 0.6, ["rabbit mq"]),
    ("openapi", "OpenAPI", "tool", 0.55, ["swagger"]),
    # --- Testing ---------------------------------------------------------
    ("pytest", "pytest", "testing", 0.65, []),
    ("jest", "Jest", "testing", 0.6, []),
    ("playwright", "Playwright", "testing", 0.55, []),
    ("unit testing", "Unit Testing", "testing", 0.75, ["unit tests"]),
    # --- Practices -------------------------------------------------------
    ("agile", "Agile", "practice", 0.7, ["scrum", "kanban"]),
    (
        "microservices",
        "Microservices",
        "practice",
        0.75,
        ["microservice", "micro services"],
    ),
    (
        "tdd",
        "Test-Driven Development",
        "practice",
        0.6,
        ["test driven development", "test-driven development"],
    ),
    # --- Soft skills -----------------------------------------------------
    ("communication", "Communication", "soft_skill", 0.6, ["communication skills"]),
    ("leadership", "Leadership", "soft_skill", 0.6, ["team lead", "tech lead"]),
    ("collaboration", "Collaboration", "soft_skill", 0.55, ["teamwork", "team player"]),
    ("problem solving", "Problem Solving", "soft_skill", 0.55, ["problem-solving"]),
]


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_lexicon() -> dict[str, Any]:
    """Assemble the lexicon document from the curated data, deterministically.

    Validates the curated data for internal consistency (duplicate canonical
    terms, alias collisions, weight range) so a typo in the table above fails
    the build instead of silently shipping a broken artifact.
    """
    seen_canonical: set[str] = set()
    seen_alias: dict[str, str] = {}
    skills: list[dict[str, Any]] = []

    for canonical, display, category, weight, aliases in _SKILLS:
        key = canonical.strip().lower()
        if not key:
            raise ValueError("empty canonical term in curated data")
        if key in seen_canonical:
            raise ValueError(f"duplicate canonical term: {key!r}")
        seen_canonical.add(key)

        if not 0.0 < weight <= 1.0:
            raise ValueError(f"weight for {key!r} out of range (0, 1]: {weight}")

        # Normalize + de-duplicate aliases; an alias may not equal its own
        # canonical and may not be claimed by two different canonical terms.
        norm_aliases: list[str] = []
        for alias in aliases:
            a = alias.strip().lower()
            if not a or a == key:
                continue
            if a in seen_alias and seen_alias[a] != key:
                raise ValueError(
                    f"alias {a!r} maps to both {seen_alias[a]!r} and {key!r}"
                )
            if a in seen_canonical and a != key:
                raise ValueError(f"alias {a!r} collides with a canonical term")
            seen_alias[a] = key
            if a not in norm_aliases:
                norm_aliases.append(a)

        skills.append(
            {
                "canonical": key,
                "display": display,
                "category": category,
                "weight": round(float(weight), 4),
                "aliases": sorted(norm_aliases),
            }
        )

    # Sort skills by canonical term so the serialized output is stable
    # regardless of the curated table's row order.
    skills.sort(key=lambda s: s["canonical"])

    return {
        "schema_version": SCHEMA_VERSION,
        "lexicon_version": LEXICON_VERSION,
        "description": (
            "MatchLayer Phase 1 Skill_Lexicon. Canonical skills, alias rules, "
            "per-term weights and metadata for the deterministic non-LLM "
            "Match_Scorer. Source of truth: ml/lexicon/skill_lexicon.v1.json; "
            "regenerate with ml/pipelines/build_skill_lexicon.py."
        ),
        "source": "ml/pipelines/build_skill_lexicon.py",
        "skill_count": len(skills),
        "skills": skills,
    }


def serialize(document: dict[str, Any]) -> str:
    """Serialize the lexicon to canonical, byte-stable JSON text.

    ``sort_keys`` + fixed indentation + a trailing newline make the output
    reproducible across machines and Python versions, which is what lets the
    drift check assert byte-for-byte equality.
    """
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate the MatchLayer Skill_Lexicon artifact."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Do not write; exit 1 if either committed artifact differs from "
            "what this script would emit."
        ),
    )
    args = parser.parse_args(argv)

    content = serialize(build_lexicon())
    targets = (SOURCE_ARTIFACT, PACKAGE_ARTIFACT)

    if args.check:
        drifted = [
            p
            for p in targets
            if not p.exists() or p.read_text(encoding="utf-8") != content
        ]
        if drifted:
            sys.stderr.write(
                "error: skill_lexicon artifacts are stale or missing:\n"
                + "".join(f"    - {p.relative_to(REPO_ROOT)}\n" for p in drifted)
                + "\nRun: python3 ml/pipelines/build_skill_lexicon.py\n"
            )
            return 1
        print("OK: skill_lexicon artifacts match the curated source.")
        return 0

    for path in targets:
        _write(path, content)
    print(
        f"Wrote skill_lexicon v{LEXICON_VERSION} "
        f"({build_lexicon()['skill_count']} skills) to:\n"
        f"    - {SOURCE_ARTIFACT.relative_to(REPO_ROOT)}\n"
        f"    - {PACKAGE_ARTIFACT.relative_to(REPO_ROOT)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
