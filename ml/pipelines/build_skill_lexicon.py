#!/usr/bin/env python3
"""Regenerate the MatchLayer Skill_Lexicon artifact (phase-2-nlp-embeddings).

Anchors:
    * ``.kiro/specs/phase-2-nlp-embeddings/requirements.md`` → Requirement 5
      (5.1 documented open sources + byte-identical reruns, 5.5 added/removed
      diff summary, 5.6 invariant validation with non-zero exit and no write,
      5.7 preserved ``--check`` drift mode).
    * ``.kiro/specs/phase-2-nlp-embeddings/design.md`` → §11 "Lexicon pipeline
      v2" and decision D12 (ESCO plus the curated Phase 1 seed).
    * ``.kiro/specs/phase-1-matching/requirements.md`` → Requirement 10.3,
      10.4 (committed, versioned artifact; source of truth under ``ml/``;
      regeneration script under ``ml/pipelines/``, never imported by the API
      at runtime).
    * ``.kiro/steering/structure.md`` → "the API imports trained artifacts,
      not training code".

What this script does
---------------------
It assembles the Skill_Lexicon from the documented open sources below — the
curated Phase 1 seed plus a curated subset of the ESCO skills taxonomy, both
vendored inline so the build needs **no network access** and reruns are
**byte-identical** — validates the Phase 1 artifact invariants, and
serializes the result deterministically (sorted keys, sorted skills, sorted
aliases, canonical JSON) to the canonical source artifact:

    ml/lexicon/skill_lexicon.v2.json

and then copies those exact bytes to the package-data artifact the API ships:

    apps/api/src/matchlayer_api/scoring/data/skill_lexicon.v2.json

The two files are byte-identical by construction; CI enforces that they stay
that way via ``tools/check_lexicon_drift.py``. On every build the script also
prints an added/removed canonical-term diff against the previously committed
artifact (the committed v2 copy when present, otherwise the v1 artifact), so
lexicon growth is reviewable in the pull request that commits a new artifact
(Requirement 5.5).

If the assembled data violates a Phase 1 invariant — duplicate canonical
term, an alias mapped to more than one canonical term, an alias colliding
with a canonical term, or a weight outside ``0 < w <= 1`` — the script exits
non-zero **without writing anything** (Requirement 5.6).

Sources (Requirement 5.1)
-------------------------
Each source is recorded with its name, version or retrieval date, and
license, both here and in the emitted artifact's ``sources`` field:

1. **MatchLayer curated seed (Phase 1)** — the hand-curated skill table
   shipped as ``skill_lexicon.v1.json`` (lexicon version 1.0.0). Authored
   in-repo; same license as the repository. No LLM, no scraped source.
2. **ESCO — European Skills, Competences, Qualifications and Occupations,
   skills pillar (curated subset)** — version 1.2.1, retrieved 2026-07-26
   from https://esco.ec.europa.eu/. Reuse of the ESCO skills/competences
   content is permitted under the Creative Commons Attribution 4.0
   International (CC BY 4.0) licence per the ESCO copyright notice
   (https://esco.ec.europa.eu/en/copyright-notice-esco-skills-competences).
   The subset below was transcribed by hand from ESCO skill concepts
   relevant to software/tech resumes and is vendored inline so builds are
   deterministic and offline; weights and category assignments are
   MatchLayer curation, not part of ESCO.

Merge policy (deterministic by construction):

* Sources are processed in the declared order; the earlier source wins.
* A canonical term already defined by an earlier source is skipped entirely.
* An alias already claimed by an earlier source (or equal to an
  earlier-declared canonical term) is dropped from the later entry.
* Conflicts *within* a single source are hard errors (a data bug), and the
  fully assembled result is re-validated against all four Phase 1
  invariants before anything is written.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Versioning
# ---------------------------------------------------------------------------

# The artifact schema version. Bump only when the *shape* of the JSON changes
# (the loader in scoring/lexicon.py reads against this). Distinct from the
# lexicon content version below. Phase 2 keeps the Phase 1 shape (Req 5.2:
# same schema version, loader unchanged).
SCHEMA_VERSION = 1

# The lexicon content version. This string flows into the Scorer_Version
# (Requirement 5.3 / 6.1), so a change to the curated data below MUST bump
# this so previously-persisted match_results remain attributable to the
# lexicon that produced them. Phase 2 ships "v2" (design §11).
LEXICON_VERSION = "v2"


# ---------------------------------------------------------------------------
# Paths (resolved relative to this file: ml/pipelines/build_skill_lexicon.py)
# ---------------------------------------------------------------------------

REPO_ROOT: Path = Path(__file__).resolve().parent.parent.parent
_PACKAGE_DATA_DIR: Path = (
    REPO_ROOT / "apps" / "api" / "src" / "matchlayer_api" / "scoring" / "data"
)
# Canonical source of truth (lives under ml/, per structure.md).
SOURCE_ARTIFACT: Path = (
    REPO_ROOT / "ml" / "lexicon" / f"skill_lexicon.{LEXICON_VERSION}.json"
)
# Committed package-data copy the API ships and loads via importlib.resources.
PACKAGE_ARTIFACT: Path = _PACKAGE_DATA_DIR / f"skill_lexicon.{LEXICON_VERSION}.json"

# Candidates for the "previously committed artifact" the diff summary
# (Requirement 5.5) compares against: the committed copy of the version this
# script emits when it exists (a rerun), otherwise the newest prior version.
PREVIOUS_ARTIFACT_CANDIDATES: tuple[Path, ...] = (
    SOURCE_ARTIFACT,
    REPO_ROOT / "ml" / "lexicon" / "skill_lexicon.v1.json",
)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class LexiconInvariantError(ValueError):
    """The assembled lexicon data violates a Phase 1 artifact invariant.

    Raised for: a duplicate canonical term, an alias mapped to more than one
    canonical term, an alias colliding with a canonical term, or a weight
    outside ``0 < w <= 1`` (Requirement 5.6). ``main`` converts this into a
    non-zero exit **before** any artifact is written.
    """


# ---------------------------------------------------------------------------
# Source model
# ---------------------------------------------------------------------------

# Each skill entry is (canonical, display, category, weight, aliases).
#
#   canonical — the normalized key used for matching (lowercase, no aliases).
#   display   — human-friendly label for the UI / suggestion templates.
#   category  — coarse grouping; suggestion templates key off this.
#   weight    — relative importance (0 < w <= 1). Keyword/suggestion lists are
#               ordered by descending weight, so in-demand core skills surface
#               first. Default-ish skills sit around 0.5; the most universally
#               requested at 1.0.
#   aliases   — surface forms that normalize to `canonical` (case-folded).
_RawSkill = tuple[str, str, str, float, list[str]]


@dataclass(frozen=True)
class LexiconSource:
    """One documented open source feeding the lexicon (Requirement 5.1)."""

    name: str
    version: str
    retrieved: str  # ISO date the source data was retrieved/curated
    license: str
    url: str
    skills: tuple[_RawSkill, ...]


# ---------------------------------------------------------------------------
# Source 1 — MatchLayer curated seed (Phase 1)
# ---------------------------------------------------------------------------
#
# The Phase 1 hand-curated skill table, preserved verbatim (canonical terms,
# aliases, categories, and weights unchanged) so every skill Phase 1 knew
# about survives into v2. Curated by hand from common resume/JD vocabulary.
# No LLM, no scraped source.

_CURATED_V1_SKILLS: tuple[_RawSkill, ...] = (
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
)


# ---------------------------------------------------------------------------
# Source 2 — ESCO skills pillar (curated subset)
# ---------------------------------------------------------------------------
#
# Hand-transcribed subset of ESCO v1.2.1 skill/knowledge concepts relevant to
# software and tech resumes (ICT skills, data skills, and ESCO transversal
# skills). Vendored inline so the build is deterministic and offline (no
# network access at build time). Canonical spellings are normalized to the
# lexicon's lowercase convention; weights and categories are MatchLayer
# curation layered on top of the ESCO vocabulary, not part of ESCO itself.
#
# Reuse licence: CC BY 4.0 per the ESCO copyright notice for
# skills/competences content —
# https://esco.ec.europa.eu/en/copyright-notice-esco-skills-competences

_ESCO_SKILLS: tuple[_RawSkill, ...] = (
    # --- Languages / markup ----------------------------------------------
    ("html", "HTML", "language", 0.8, ["html5"]),
    ("css", "CSS", "language", 0.8, ["css3"]),
    ("r", "R", "language", 0.6, ["r programming"]),
    ("matlab", "MATLAB", "language", 0.5, []),
    ("perl", "Perl", "language", 0.4, []),
    ("objective-c", "Objective-C", "language", 0.5, ["objective c"]),
    ("dart", "Dart", "language", 0.5, []),
    ("elixir", "Elixir", "language", 0.45, []),
    ("haskell", "Haskell", "language", 0.4, []),
    ("lua", "Lua", "language", 0.35, []),
    ("groovy", "Groovy", "language", 0.4, []),
    ("powershell", "PowerShell", "language", 0.55, []),
    ("cobol", "COBOL", "language", 0.3, []),
    ("fortran", "Fortran", "language", 0.3, []),
    ("julia", "Julia", "language", 0.4, []),
    ("solidity", "Solidity", "language", 0.4, []),
    ("visual basic", "Visual Basic", "language", 0.3, ["vb.net", "vba"]),
    ("sass", "Sass", "language", 0.5, ["scss"]),
    ("json", "JSON", "tool", 0.5, []),
    ("xml", "XML", "tool", 0.4, []),
    ("yaml", "YAML", "tool", 0.4, ["yml"]),
    # --- Mobile ----------------------------------------------------------
    ("android", "Android", "framework", 0.7, ["android development"]),
    ("ios", "iOS", "framework", 0.7, ["ios development"]),
    ("react native", "React Native", "framework", 0.65, ["react-native"]),
    ("flutter", "Flutter", "framework", 0.6, []),
    ("xamarin", "Xamarin", "framework", 0.35, []),
    (
        "mobile development",
        "Mobile Development",
        "practice",
        0.6,
        ["mobile app development"],
    ),
    # --- Frontend tooling / web ------------------------------------------
    ("webpack", "webpack", "tool", 0.5, []),
    ("vite", "Vite", "tool", 0.5, []),
    ("bootstrap", "Bootstrap", "framework", 0.5, []),
    ("jquery", "jQuery", "library", 0.4, []),
    ("web accessibility", "Web Accessibility", "practice", 0.55, ["wcag", "a11y"]),
    (
        "responsive design",
        "Responsive Design",
        "practice",
        0.5,
        ["responsive web design"],
    ),
    (
        "web development",
        "Web Development",
        "practice",
        0.7,
        ["web application development"],
    ),
    # --- Backend / APIs ---------------------------------------------------
    ("laravel", "Laravel", "framework", 0.55, []),
    ("symfony", "Symfony", "framework", 0.4, []),
    ("nestjs", "NestJS", "framework", 0.55, ["nest.js", "nest js"]),
    ("hibernate", "Hibernate", "framework", 0.5, []),
    ("sqlalchemy", "SQLAlchemy", "library", 0.55, []),
    ("websockets", "WebSockets", "tool", 0.5, ["websocket"]),
    ("oauth", "OAuth", "tool", 0.6, ["oauth2", "oauth 2.0", "openid connect", "oidc"]),
    ("soap", "SOAP", "tool", 0.35, ["soap api"]),
    # --- Databases / data platforms --------------------------------------
    (
        "oracle",
        "Oracle Database",
        "database",
        0.6,
        ["oracle database", "oracle db", "pl/sql", "plsql"],
    ),
    (
        "sql server",
        "Microsoft SQL Server",
        "database",
        0.65,
        ["microsoft sql server", "mssql", "t-sql", "tsql"],
    ),
    ("cassandra", "Apache Cassandra", "database", 0.5, ["apache cassandra"]),
    ("neo4j", "Neo4j", "database", 0.4, []),
    ("snowflake", "Snowflake", "database", 0.6, []),
    ("bigquery", "BigQuery", "database", 0.55, ["google bigquery"]),
    ("redshift", "Amazon Redshift", "database", 0.5, ["amazon redshift"]),
    ("database administration", "Database Administration", "database", 0.5, ["dba"]),
    # --- Data / ML --------------------------------------------------------
    ("data warehousing", "Data Warehousing", "data", 0.6, ["data warehouse"]),
    ("etl", "ETL", "data", 0.65, ["extract transform load", "elt"]),
    ("data modeling", "Data Modeling", "data", 0.6, ["data modelling"]),
    ("data analysis", "Data Analysis", "data", 0.7, ["data analytics"]),
    ("data visualization", "Data Visualization", "data", 0.6, ["data visualisation"]),
    ("tableau", "Tableau", "tool", 0.55, []),
    ("power bi", "Power BI", "tool", 0.6, ["powerbi", "microsoft power bi"]),
    ("dbt", "dbt", "tool", 0.5, []),
    ("hadoop", "Apache Hadoop", "data", 0.45, ["apache hadoop", "hdfs"]),
    ("databricks", "Databricks", "data", 0.55, []),
    (
        "data engineering",
        "Data Engineering",
        "data",
        0.65,
        ["data pipelines", "data pipeline"],
    ),
    ("statistics", "Statistics", "data", 0.6, ["statistical analysis"]),
    (
        "deep learning",
        "Deep Learning",
        "data",
        0.65,
        ["neural networks", "neural network"],
    ),
    ("computer vision", "Computer Vision", "data", 0.55, ["image processing"]),
    ("data science", "Data Science", "data", 0.7, []),
    ("big data", "Big Data", "data", 0.55, []),
    ("keras", "Keras", "data", 0.45, []),
    ("mlops", "MLOps", "data", 0.55, ["ml ops"]),
    ("hugging face", "Hugging Face", "data", 0.5, ["huggingface"]),
    # --- DevOps / cloud ----------------------------------------------------
    ("devops", "DevOps", "practice", 0.8, []),
    ("ansible", "Ansible", "devops", 0.55, []),
    ("puppet", "Puppet", "devops", 0.35, []),
    ("chef", "Chef", "devops", 0.35, []),
    ("gitlab", "GitLab", "devops", 0.5, ["gitlab ci", "gitlab ci/cd"]),
    ("bitbucket", "Bitbucket", "devops", 0.35, []),
    ("prometheus", "Prometheus", "devops", 0.55, []),
    ("grafana", "Grafana", "devops", 0.55, []),
    ("datadog", "Datadog", "tool", 0.5, []),
    ("splunk", "Splunk", "tool", 0.45, []),
    ("cloudformation", "AWS CloudFormation", "devops", 0.5, ["aws cloudformation"]),
    ("helm", "Helm", "devops", 0.5, ["helm charts"]),
    ("istio", "Istio", "devops", 0.35, []),
    ("openshift", "OpenShift", "devops", 0.4, []),
    ("serverless", "Serverless", "cloud", 0.55, ["serverless architecture"]),
    ("observability", "Observability", "devops", 0.5, []),
    (
        "site reliability engineering",
        "Site Reliability Engineering",
        "practice",
        0.55,
        ["sre"],
    ),
    ("infrastructure as code", "Infrastructure as Code", "devops", 0.65, ["iac"]),
    (
        "cloud computing",
        "Cloud Computing",
        "cloud",
        0.7,
        ["cloud architecture", "cloud infrastructure"],
    ),
    ("windows server", "Windows Server", "devops", 0.4, []),
    # --- Security ---------------------------------------------------------
    (
        "cybersecurity",
        "Cybersecurity",
        "practice",
        0.7,
        ["cyber security", "information security", "infosec"],
    ),
    (
        "penetration testing",
        "Penetration Testing",
        "practice",
        0.5,
        ["pen testing", "pentesting"],
    ),
    ("application security", "Application Security", "practice", 0.55, ["appsec"]),
    ("network security", "Network Security", "practice", 0.5, []),
    ("cryptography", "Cryptography", "practice", 0.5, ["encryption"]),
    (
        "identity and access management",
        "Identity and Access Management",
        "practice",
        0.5,
        ["iam"],
    ),
    ("devsecops", "DevSecOps", "practice", 0.45, []),
    ("incident response", "Incident Response", "practice", 0.5, []),
    ("vulnerability management", "Vulnerability Management", "practice", 0.45, []),
    # --- Networking --------------------------------------------------------
    (
        "networking",
        "Networking",
        "tool",
        0.6,
        ["computer networking", "tcp/ip", "tcp ip"],
    ),
    ("dns", "DNS", "tool", 0.4, []),
    ("load balancing", "Load Balancing", "tool", 0.45, ["load balancer"]),
    ("vpn", "VPN", "tool", 0.35, []),
    # --- Testing ------------------------------------------------------------
    ("selenium", "Selenium", "testing", 0.5, []),
    ("cypress", "Cypress", "testing", 0.5, []),
    (
        "integration testing",
        "Integration Testing",
        "testing",
        0.6,
        ["integration tests"],
    ),
    (
        "end-to-end testing",
        "End-to-End Testing",
        "testing",
        0.55,
        ["e2e testing", "end to end testing"],
    ),
    ("test automation", "Test Automation", "testing", 0.6, ["automated testing"]),
    ("performance testing", "Performance Testing", "testing", 0.5, ["load testing"]),
    ("quality assurance", "Quality Assurance", "testing", 0.6, ["qa"]),
    # --- Engineering practices ----------------------------------------------
    ("code review", "Code Review", "practice", 0.6, ["code reviews"]),
    ("pair programming", "Pair Programming", "practice", 0.4, []),
    ("design patterns", "Design Patterns", "practice", 0.6, []),
    (
        "object-oriented programming",
        "Object-Oriented Programming",
        "practice",
        0.65,
        ["oop", "object oriented programming"],
    ),
    ("functional programming", "Functional Programming", "practice", 0.5, []),
    (
        "data structures",
        "Data Structures",
        "practice",
        0.6,
        ["data structures and algorithms"],
    ),
    ("algorithms", "Algorithms", "practice", 0.6, []),
    ("distributed systems", "Distributed Systems", "practice", 0.7, []),
    (
        "event-driven architecture",
        "Event-Driven Architecture",
        "practice",
        0.55,
        ["event driven architecture"],
    ),
    (
        "domain-driven design",
        "Domain-Driven Design",
        "practice",
        0.5,
        ["ddd", "domain driven design"],
    ),
    ("api design", "API Design", "practice", 0.6, []),
    (
        "software architecture",
        "Software Architecture",
        "practice",
        0.7,
        ["solution architecture"],
    ),
    ("system design", "System Design", "practice", 0.65, []),
    ("technical documentation", "Technical Documentation", "practice", 0.5, []),
    ("debugging", "Debugging", "practice", 0.55, ["troubleshooting"]),
    ("refactoring", "Refactoring", "practice", 0.5, ["code refactoring"]),
    ("embedded systems", "Embedded Systems", "practice", 0.5, ["embedded software"]),
    # --- Project / product ---------------------------------------------------
    (
        "project management",
        "Project Management",
        "practice",
        0.65,
        ["project planning"],
    ),
    ("product management", "Product Management", "practice", 0.55, []),
    (
        "stakeholder management",
        "Stakeholder Management",
        "soft_skill",
        0.5,
        ["stakeholder engagement"],
    ),
    ("jira", "Jira", "tool", 0.5, []),
    ("confluence", "Confluence", "tool", 0.4, []),
    # --- Design / UX ---------------------------------------------------------
    ("figma", "Figma", "tool", 0.5, []),
    ("ui design", "UI Design", "practice", 0.55, ["user interface design"]),
    (
        "ux design",
        "UX Design",
        "practice",
        0.6,
        ["user experience design", "ux research"],
    ),
    # --- Business tools / domains --------------------------------------------
    (
        "seo",
        "SEO",
        "practice",
        0.45,
        ["search engine optimization", "search engine optimisation"],
    ),
    ("salesforce", "Salesforce", "tool", 0.5, []),
    ("sap", "SAP", "tool", 0.45, []),
    ("excel", "Microsoft Excel", "tool", 0.5, ["microsoft excel", "spreadsheets"]),
    ("blockchain", "Blockchain", "practice", 0.45, []),
    ("iot", "IoT", "practice", 0.4, ["internet of things"]),
    ("unity", "Unity", "framework", 0.45, ["unity3d"]),
    ("unreal engine", "Unreal Engine", "framework", 0.4, ["unreal"]),
    # --- Transversal (ESCO) skills -------------------------------------------
    ("mentoring", "Mentoring", "soft_skill", 0.5, ["mentorship", "coaching"]),
    ("time management", "Time Management", "soft_skill", 0.45, []),
    ("critical thinking", "Critical Thinking", "soft_skill", 0.5, []),
    ("adaptability", "Adaptability", "soft_skill", 0.45, ["flexibility"]),
    ("creativity", "Creativity", "soft_skill", 0.4, []),
    (
        "attention to detail",
        "Attention to Detail",
        "soft_skill",
        0.45,
        ["detail oriented", "detail-oriented"],
    ),
    (
        "presentation skills",
        "Presentation Skills",
        "soft_skill",
        0.45,
        ["public speaking"],
    ),
    ("negotiation", "Negotiation", "soft_skill", 0.4, []),
    ("customer service", "Customer Service", "soft_skill", 0.45, ["customer support"]),
    (
        "analytical skills",
        "Analytical Skills",
        "soft_skill",
        0.55,
        ["analytical thinking"],
    ),
    ("decision making", "Decision Making", "soft_skill", 0.45, ["decision-making"]),
    ("conflict resolution", "Conflict Resolution", "soft_skill", 0.4, []),
    ("emotional intelligence", "Emotional Intelligence", "soft_skill", 0.4, []),
)


# ---------------------------------------------------------------------------
# Declared sources, in precedence order (earlier source wins on conflicts)
# ---------------------------------------------------------------------------

SOURCES: tuple[LexiconSource, ...] = (
    LexiconSource(
        name="MatchLayer curated seed (Phase 1)",
        version="1.0.0",
        retrieved="2026-07-26",
        license="Repository license (see LICENSE)",
        url="ml/pipelines/build_skill_lexicon.py",
        skills=_CURATED_V1_SKILLS,
    ),
    LexiconSource(
        name=(
            "ESCO — European Skills, Competences, Qualifications and "
            "Occupations, skills pillar (curated subset)"
        ),
        version="1.2.1",
        retrieved="2026-07-26",
        license=(
            "CC BY 4.0 (ESCO copyright notice for skills/competences: "
            "https://esco.ec.europa.eu/en/copyright-notice-esco-skills-competences)"
        ),
        url="https://esco.ec.europa.eu/",
        skills=_ESCO_SKILLS,
    ),
)


# ---------------------------------------------------------------------------
# Assembly + validation
# ---------------------------------------------------------------------------


def _normalize_term(term: str) -> str:
    """Lowercase and collapse internal whitespace, mirroring the loader."""
    return " ".join(term.lower().split())


def assemble_skills(sources: tuple[LexiconSource, ...]) -> list[dict[str, Any]]:
    """Merge the declared sources into one deterministic skill list.

    Merge policy (documented in the module docstring): sources are processed
    in declared order and the earlier source wins — a later source's entry
    for an already-defined canonical term is skipped, and a later source's
    alias that is already claimed (by any earlier alias or canonical term) is
    dropped. Conflicts *within* a single source raise
    :class:`LexiconInvariantError` because they are data bugs, not merge
    events. The result is sorted by canonical term so serialization is stable
    regardless of table row order.
    """
    seen_canonical: dict[str, str] = {}  # canonical -> source name
    seen_alias: dict[str, str] = {}  # alias -> canonical
    alias_source: dict[str, str] = {}  # alias -> source name that claimed it
    skills: list[dict[str, Any]] = []

    for source in sources:
        for canonical, display, category, weight, aliases in source.skills:
            key = _normalize_term(canonical)
            if not key:
                raise LexiconInvariantError(
                    f"empty canonical term in source {source.name!r}"
                )
            if key in seen_canonical:
                if seen_canonical[key] == source.name:
                    raise LexiconInvariantError(
                        f"duplicate canonical term within source "
                        f"{source.name!r}: {key!r}"
                    )
                # Cross-source duplicate: the earlier source wins (skip).
                continue
            if key in seen_alias:
                # A canonical colliding with an alias an earlier source
                # already claimed is a curation conflict needing a human
                # decision, not a silent merge.
                raise LexiconInvariantError(
                    f"canonical term {key!r} from source {source.name!r} "
                    f"collides with an alias of {seen_alias[key]!r}"
                )
            if not 0.0 < weight <= 1.0:
                raise LexiconInvariantError(
                    f"weight for {key!r} out of range (0, 1]: {weight}"
                )
            seen_canonical[key] = source.name

            norm_aliases: list[str] = []
            for alias in aliases:
                a = _normalize_term(alias)
                if not a or a == key:
                    continue
                if a in seen_alias and seen_alias[a] != key:
                    if alias_source[a] == source.name:
                        raise LexiconInvariantError(
                            f"alias {a!r} maps to both {seen_alias[a]!r} and "
                            f"{key!r} within source {source.name!r}"
                        )
                    # Claimed by an earlier source: drop from this entry.
                    continue
                if a in seen_canonical:
                    if seen_canonical[a] == source.name:
                        raise LexiconInvariantError(
                            f"alias {a!r} collides with a canonical term "
                            f"within source {source.name!r}"
                        )
                    # Equal to an earlier source's canonical term: drop.
                    continue
                seen_alias[a] = key
                alias_source[a] = source.name
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

    skills.sort(key=lambda s: str(s["canonical"]))
    return skills


def validate_invariants(skills: list[dict[str, Any]]) -> None:
    """Validate the assembled data against the Phase 1 artifact invariants.

    Raises :class:`LexiconInvariantError` on the first violation found:
    a duplicate canonical term, an alias mapped to more than one canonical
    term, an alias colliding with a canonical term, or a weight outside
    ``0 < w <= 1`` (Requirement 5.6). The authoritative final gate before
    anything is written, independent of the merge policy above.
    """
    canonicals: set[str] = set()
    for skill in skills:
        canonical = str(skill["canonical"])
        if canonical in canonicals:
            raise LexiconInvariantError(f"duplicate canonical term: {canonical!r}")
        canonicals.add(canonical)

    alias_owner: dict[str, str] = {}
    for skill in skills:
        canonical = str(skill["canonical"])
        weight = skill["weight"]
        if not isinstance(weight, (int, float)) or not 0.0 < float(weight) <= 1.0:
            raise LexiconInvariantError(
                f"weight for {canonical!r} out of range (0, 1]: {weight!r}"
            )
        for alias in skill["aliases"]:
            a = str(alias)
            if a in canonicals:
                raise LexiconInvariantError(
                    f"alias {a!r} of {canonical!r} collides with a canonical term"
                )
            if a in alias_owner and alias_owner[a] != canonical:
                raise LexiconInvariantError(
                    f"alias {a!r} maps to both {alias_owner[a]!r} and {canonical!r}"
                )
            alias_owner[a] = canonical


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def build_lexicon() -> dict[str, Any]:
    """Assemble and validate the lexicon document, deterministically.

    Raises :class:`LexiconInvariantError` (and produces no document) when the
    assembled data violates a Phase 1 invariant, so the CLI exits non-zero
    without writing (Requirement 5.6).
    """
    skills = assemble_skills(SOURCES)
    validate_invariants(skills)

    return {
        "schema_version": SCHEMA_VERSION,
        "lexicon_version": LEXICON_VERSION,
        "description": (
            "MatchLayer Phase 2 Skill_Lexicon. Canonical skills, alias rules, "
            "per-term weights and metadata for the skill-only Skill_Extractor "
            "and the deterministic fallback Match_Scorer. Assembled from the "
            "documented open sources listed under 'sources'. Source of truth: "
            f"ml/lexicon/skill_lexicon.{LEXICON_VERSION}.json; regenerate "
            "with ml/pipelines/build_skill_lexicon.py."
        ),
        "source": "ml/pipelines/build_skill_lexicon.py",
        "sources": [
            {
                "name": source.name,
                "version": source.version,
                "retrieved": source.retrieved,
                "license": source.license,
                "url": source.url,
            }
            for source in SOURCES
        ],
        "skill_count": len(skills),
        "skills": skills,
    }


def serialize(document: dict[str, Any]) -> str:
    """Serialize the lexicon to canonical, byte-stable JSON text.

    ``sort_keys`` + fixed indentation + a trailing newline make the output
    reproducible across machines and Python versions, which is what lets the
    drift check assert byte-for-byte equality (Requirement 5.1).
    """
    return json.dumps(document, indent=2, ensure_ascii=False, sort_keys=True) + "\n"


# ---------------------------------------------------------------------------
# Diff summary vs the previously committed artifact (Requirement 5.5)
# ---------------------------------------------------------------------------


def _load_previous_artifact() -> tuple[str, set[str]] | None:
    """Read the previously committed artifact's version and canonical terms.

    Prefers the committed copy of the version this script emits (a rerun
    diffs against itself as committed); falls back to the newest prior
    version (v1) so the first v2 build reports growth relative to v1.
    Returns ``None`` when no committed artifact exists or one is unreadable.
    """
    for path in PREVIOUS_ARTIFACT_CANDIDATES:
        if not path.exists():
            continue
        try:
            raw: Any = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(raw, dict) or not isinstance(raw.get("skills"), list):
            return None
        version = str(raw.get("lexicon_version", "unknown"))
        canonicals = {
            str(skill["canonical"])
            for skill in raw["skills"]
            if isinstance(skill, dict) and "canonical" in skill
        }
        return (
            f"{path.relative_to(REPO_ROOT)} (lexicon_version {version})",
            canonicals,
        )
    return None


def render_diff_summary(
    new_canonicals: set[str], previous: tuple[str, set[str]] | None
) -> str:
    """Human-readable added/removed diff for the build summary (Req 5.5)."""
    if previous is None:
        return "No previously committed artifact found; skipping diff summary."

    label, old_canonicals = previous
    added = sorted(new_canonicals - old_canonicals)
    removed = sorted(old_canonicals - new_canonicals)

    lines = [f"Diff vs previously committed {label}:"]
    lines.append(f"  canonical terms added:   {len(added)}")
    lines.extend(f"    + {term}" for term in added)
    lines.append(f"  canonical terms removed: {len(removed)}")
    lines.extend(f"    - {term}" for term in removed)
    return "\n".join(lines)


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

    # Assemble + validate BEFORE touching the filesystem: an invariant
    # violation exits non-zero with nothing written (Requirement 5.6).
    try:
        document = build_lexicon()
    except LexiconInvariantError as exc:
        sys.stderr.write(f"error: lexicon invariant violated: {exc}\n")
        return 1

    content = serialize(document)
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
        print("OK: skill_lexicon artifacts match the curated sources.")
        return 0

    # Snapshot the previously committed artifact before overwriting it, so
    # the diff summary (Requirement 5.5) reflects what this build changed.
    previous = _load_previous_artifact()
    new_canonicals = {str(skill["canonical"]) for skill in document["skills"]}

    for path in targets:
        _write(path, content)

    print(
        f"Wrote skill_lexicon {LEXICON_VERSION} "
        f"({document['skill_count']} skills) to:\n"
        f"    - {SOURCE_ARTIFACT.relative_to(REPO_ROOT)}\n"
        f"    - {PACKAGE_ARTIFACT.relative_to(REPO_ROOT)}"
    )
    print(render_diff_summary(new_canonicals, previous))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
