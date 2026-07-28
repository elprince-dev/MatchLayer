"""``Scoring_Service``: business logic for the match surface.

This is the ONLY module in the API permitted to read or write the
``match_results`` table (Components and Interfaces import-boundary table;
Requirement 1.4). Every query it issues carries ``WHERE user_id =
:current_user`` so a result set can never include a row owned by a different
User_Account (Requirement 1.4).

Public surface (async), mirroring the design "Scoring_Service" interface:

* :meth:`Scoring_Service.create_match` — enforce the daily ``Scoring_Quota``,
  load the owned non-deleted Resume, require it to be extractable, score it
  against the supplied Job_Description via the ``ml/`` adapter, persist a
  ``match_results`` row, and emit a ``match_created`` Audit_Event
  (Requirements 8.4, 8.5, 8.6, 11.5, 11.6).
* :meth:`Scoring_Service.list_matches` — cursor-paginated listing of the
  caller's non-deleted matches, ``created_at`` descending (Requirement 9.1).
* :meth:`Scoring_Service.get_match` — fetch a single owned match, returning it
  even when its Resume was later soft-deleted (Requirements 9.3, 9.6).
* :meth:`Scoring_Service.soft_delete_match` — idempotent soft delete that emits
  ``match_deleted`` only on the first delete (Requirements 9.4, 9.5).

Phase 2 (phase-2-nlp-embeddings, "Match request flow" + "Fallback decision
tree"): ``create_match`` scores through the semantic pipeline when it loaded
at startup — reusing the stored resume Embedding when its recorded model
name+revision match the loaded pipeline's, generating (and best-effort
upserting) it otherwise, embedding the Job_Description under the configured
timeout, scoring via the ``Semantic_Match_Scorer``, and persisting the JD
Embedding best-effort alongside the ``match_results`` row (phase-2
Requirements 2.6, 2.7, 2.8, 2.12). Every failure on that path falls back
**per-request** to the untouched Phase 1 engine with the Phase 1
``scorer_version`` stamp, no Embedding stored for the request, and exactly
one structured event from the documented category set — ``embedding_timeout``,
``embedding_runtime_error``, ``embedding_geometry_error``,
``skill_extraction_error``, ``skill_extraction_empty`` — carrying
``request_id`` (structlog contextvars) and internal ids only, never text or
vector values (phase-2 Requirements 2.9, 3.10, 4.10, 4.12, 6.2, 7.3, 7.4).
Startup Degraded_Mode (no loaded pipeline) uses the Phase 1 engine with **no**
per-request event — the once-at-startup ``model_load_failure`` event already
covers it (phase-2 Requirements 7.2, 7.6) — and a per-request fallback never
flips the process into Degraded_Mode (phase-2 Requirement 7.3). The
Idempotency-Key replay short-circuit lives in the router and returns the
stored response before this service (and thus any Phase 2 work) is invoked;
list/get/delete and pre-Phase-2 stored rows are untouched (phase-2
Requirements 6.4, 9.4).

Transaction model (mirrors :class:`~matchlayer_api.services.auth.Auth_Service`):
the service is stateless and dependency-injected; every method takes the active
request-scoped :class:`AsyncSession`, stages its rows via ``session.add`` (and
flushes when a generated id is needed before the audit row references it), and
never calls ``session.commit`` itself — the router owns the commit so the
``match_results`` row and its ``match_created`` audit row land in one
transaction (Audit Log §11.3).

PRIVACY (security.md "Data classification", Requirement 8.8): the
Job_Description is Restricted PII. It is **stored** verbatim in
``match_results.job_description_text`` (so the user can revisit it), but it is
NEVER written to a log line, an error message, or an Audit_Event payload — the
audit payloads reference internal ids only (``resume_id`` / ``match_id``). No
method in this module logs ``job_description_text`` or any substring of it.

Design reference: "Scoring_Service", "Quota enforcement", Data Models,
"Error Handling". Requirements covered: 1.4, 8.4, 8.5, 8.6, 8.8, 9.1, 9.3, 9.4,
9.5, 9.6, 11.5, 11.6.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from uuid import UUID

import structlog
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession
from uuid_utils.compat import uuid7

from matchlayer_api.config import Settings, get_settings
from matchlayer_api.core.errors import (
    NotFoundError,
    QuotaExceededError,
    ResumeNotExtractableError,
)
from matchlayer_api.db.models import MatchResult, Resume
from matchlayer_api.ml import scorer_adapter
from matchlayer_api.ml.semantic_adapter import (
    EmbeddingTimeoutError,
    SemanticPipeline,
    embed_with_timeout,
    get_semantic_pipeline,
)
from matchlayer_api.scoring.scorer import EmptyAnalyzedSetError, ScoreResult
from matchlayer_api.scoring.semantic import EmbeddingGeometryError
from matchlayer_api.services.audit import Audit_Service
from matchlayer_api.services.vector_store import (
    get_resume_embedding,
    insert_match_embedding,
    upsert_resume_embedding,
)

_log = structlog.get_logger(__name__)

# The extraction status a Resume must carry before it can be scored
# (Requirement 8.5). Mirrors the literal the Resume_Extractor writes on success.
_EXTRACTION_SUCCEEDED: Final[str] = "succeeded"

# Default page size for ``list_matches`` when the router does not supply one.
# The router validates and clamps ``limit`` to 1..100 (Requirement 9.1 lists are
# cursor-paginated); the service keeps a sane fallback so a direct caller cannot
# request an unbounded page.
_DEFAULT_LIST_LIMIT: Final[int] = 20


def _now() -> datetime:
    """Return a timezone-aware "now" in UTC.

    Centralised (mirroring ``services.auth._now``) so the soft-delete stamp and
    the quota day-boundary arithmetic share one clock, and so tests can freeze
    time by monkey-patching ``services.matching._now``.
    """
    return datetime.now(UTC)


def _start_of_utc_day(moment: datetime) -> datetime:
    """Return midnight (00:00:00) UTC on the calendar day of *moment*.

    The lower bound of the daily ``Scoring_Quota`` window: a match counts
    against today's quota when its ``created_at >= start_of_utc_day`` (design
    "Quota enforcement"). Computed from the UTC calendar date so the window is
    exact and independent of the server's local timezone (Requirement 11.5).
    """
    return datetime.combine(moment.astimezone(UTC).date(), time.min, tzinfo=UTC)


def _next_utc_midnight(moment: datetime) -> datetime:
    """Return the next 00:00:00 UTC strictly after *moment*'s calendar day.

    The instant the daily quota resets, surfaced in the 429 ``detail`` so the
    caller knows when they may retry (Requirement 11.6).
    """
    next_day: date = moment.astimezone(UTC).date() + timedelta(days=1)
    return datetime.combine(next_day, time.min, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Cursor pagination (keyset on ``(created_at DESC, id DESC)``).
#
# Offset pagination is explicitly avoided (conventions.md "Pagination"). The
# cursor is an opaque, URL-safe base64 token encoding the ``(created_at, id)``
# of the last row on the current page; the next page selects strictly "older"
# rows under the composite ``match_results_user_created_idx`` index. A
# malformed or undecodable cursor is treated as the first page rather than an
# error, so a client that mangles the token degrades gracefully.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MatchPage:
    """One page of a user's matches plus the cursor for the following page.

    ``items`` are :class:`~matchlayer_api.db.models.MatchResult` ORM rows
    ordered by ``created_at`` descending (ties broken by descending ``id``);
    the router projects them onto :class:`MatchListItem`. ``next_cursor`` is the
    opaque token to fetch the next page, or ``None`` when this is the last page.
    """

    items: list[MatchResult]
    next_cursor: str | None


def _encode_cursor(created_at: datetime, row_id: UUID) -> str:
    """Encode a ``(created_at, id)`` keyset position into an opaque token.

    The timestamp is serialised as an ISO-8601 string (timezone-aware) joined
    to the row id; the pair is URL-safe base64 encoded so it travels cleanly in
    a ``?cursor=`` query parameter.
    """
    raw = f"{created_at.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID] | None:
    """Decode an opaque cursor back into ``(created_at, id)``.

    Returns ``None`` for any malformed token (bad base64, missing separator,
    unparseable timestamp or uuid) so the caller can fall back to the first
    page rather than surfacing a 4xx for a mangled cursor.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    created_raw, sep, id_raw = raw.partition("|")
    if not sep:
        return None
    try:
        created_at = datetime.fromisoformat(created_raw)
        row_id = UUID(id_raw)
    except ValueError:
        return None
    return created_at, row_id


@dataclass(frozen=True, slots=True)
class _ScoreOutcome:
    """One scoring step's result plus what (if anything) to persist as vectors.

    ``jd_vector``/``pipeline`` are non-``None`` only when the Phase 2 semantic
    path produced ``result``: the Scoring_Service then best-effort persists the
    Job_Description Embedding stamped with the pipeline's model identity
    (phase-2 Requirements 2.6, 2.10). On every fallback path — Degraded_Mode or
    any per-request ladder rung — both are ``None``, so no Embedding is stored
    for that request (phase-2 Requirements 7.2, 7.3).
    """

    result: ScoreResult
    jd_vector: list[float] | None
    pipeline: SemanticPipeline | None


class Scoring_Service:  # noqa: N801 -- design uses the underscored class name.
    """Business logic for match creation, retrieval, listing, and deletion.

    Stateless and dependency-injected: every method takes the active
    request-scoped :class:`AsyncSession` so the ``match_results`` mutation and
    its audit row commit in the same transaction (Audit Log §11.3). The
    instance holds only the :class:`Audit_Service` and the active
    :class:`Settings` (for the daily quota); no session is cached on ``self``.

    A single instance is constructed per request by the router. Construction is
    cheap, so per-request allocation is fine; tests may also construct one
    inline and pass a recording audit stub.
    """

    __slots__ = ("_audit", "_settings")

    def __init__(
        self,
        *,
        audit: Audit_Service | None = None,
        settings: Settings | None = None,
    ) -> None:
        # ``Audit_Service`` is stateless, so the production default is fine;
        # tests that assert on emitted events pass a recording stub.
        self._audit = audit if audit is not None else Audit_Service()
        self._settings = settings if settings is not None else get_settings()

    # ------------------------------------------------------------------
    # create_match (Requirements 8.4, 8.5, 8.6, 8.8, 11.5, 11.6).
    # ------------------------------------------------------------------

    async def create_match(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        resume_id: UUID,
        job_description: str,
    ) -> MatchResult:
        """Score ``resume_id`` against ``job_description`` and persist the result.

        Orchestration order is exactly the one the design "Match data flow"
        sequence and the task prescribe:

        1. **Scoring_Quota** — count this user's ``match_results`` created since
           00:00 UTC today; when the count is at or above
           ``MATCHLAYER_MATCH_DAILY_QUOTA`` emit a ``quota_rejected`` audit row
           (``quota="scoring"``) and raise :class:`QuotaExceededError` with a
           ``detail`` naming the limit and the UTC reset time. No scoring or
           insert happens (Requirements 11.5, 11.6).
        2. **Load the Resume** scoped to ``user_id`` and ``deleted_at IS NULL``;
           a missing / soft-deleted / other-owner row raises
           :class:`NotFoundError` (404 ``not_found``), which does not disclose
           whether the row exists for another account (Requirement 8.4).
        3. **Extractability** — the Resume's ``extraction_status`` must be
           ``'succeeded'``; otherwise raise :class:`ResumeNotExtractableError`
           (422 ``resume_not_extractable``) and create nothing (Requirement
           8.5).
        4. **Score** via :meth:`_score_with_fallback`: the Phase 2 semantic
           pipeline (stored-Embedding reuse, timeout-bounded embeds, the
           ``Semantic_Match_Scorer``) when it loaded at startup, with every
           failure completing the request through the Phase 1 engine per the
           design fallback decision tree — never a 5xx (phase-2 Requirements
           2.6-2.8, 7.2, 7.3). The returned ``ScoreResult`` is immutable and
           carries the ``scorer_version`` stamp of whichever engine produced
           it (phase-2 Requirements 6.1, 6.2).
        5. **Persist** a ``match_results`` row recording ``user_id``,
           ``resume_id``, the Restricted ``job_description_text``, and the
           scorer outputs serialised to their JSONB shapes, then emit a
           ``match_created`` audit row referencing the ids only (Requirements
           8.6, 8.8).

        The returned row is staged (and flushed so its generated ``id`` is
        available for the audit payload) but not committed — the router commits.

        Args:
            session: Active request-scoped session.
            user_id: The authenticated principal's id; every query is scoped to
                it (Requirement 1.4).
            resume_id: The resume to score, resolved server-side against
                ``user_id``.
            job_description: The pasted Job_Description text. Length bounds are
                enforced upstream by the request schema; this method stores it
                verbatim and never logs it (Requirement 8.8).

        Returns:
            MatchResult: the newly created, flushed row.

        Raises:
            QuotaExceededError: Daily Scoring_Quota reached (Requirement 11.6).
            NotFoundError: Resume missing / deleted / owned by another user
                (Requirement 8.4).
            ResumeNotExtractableError: Resume not in ``'succeeded'`` extraction
                state (Requirement 8.5).
        """
        # 1. Scoring_Quota — count-based, exact, UTC-day-bounded (design
        #    "Quota enforcement"). Checked before any work so a quota-rejected
        #    request performs no scoring and no insert (Requirement 11.6).
        await self._enforce_scoring_quota(session, user_id=user_id)

        # 2. Load the resume scoped to the owner and not soft-deleted. The same
        #    ``not_found`` envelope is returned whether the row is absent,
        #    soft-deleted, or owned by someone else (Requirement 8.4).
        resume = await self._load_owned_resume(session, user_id=user_id, resume_id=resume_id)

        # 3. Require successful extraction. A pending/failed resume cannot be
        #    scored; the analysis would be meaningless (Requirement 8.5).
        if resume.extraction_status != _EXTRACTION_SUCCEEDED:
            raise ResumeNotExtractableError(
                "This resume's text could not be extracted, so it cannot be "
                "scored. Upload a resume whose text extraction succeeded."
            )

        # 4. Score. ``extracted_text`` is non-null on a succeeded extraction
        #    (Requirement 3.4); fall back to an empty string defensively so the
        #    scorer's empty-input contract applies rather than passing ``None``
        #    into the scoring core. The Phase 2 semantic path (with its full
        #    per-request fallback ladder) lives in ``_score_with_fallback``;
        #    every path completes the request — never a 5xx (phase-2 Req 7.3).
        outcome = await self._score_with_fallback(
            session,
            user_id=user_id,
            resume_id=resume_id,
            resume_text=resume.extracted_text or "",
            job_description=job_description,
        )
        result = outcome.result

        # 5. Persist. The JSONB columns store plain dict/list structures the
        #    scorer's frozen dataclasses map onto field-for-field; this is the
        #    shape ``MatchResponse``/``ScoreBreakdownOut``/``KeywordOut``/
        #    ``SuggestionOut`` validate against on the way out.
        now = _now()
        match = MatchResult(
            id=uuid7(),
            user_id=user_id,
            resume_id=resume_id,
            job_description_text=job_description,
            score=result.score,
            score_breakdown={
                "similarity_component": result.breakdown.similarity_component,
                "keyword_coverage_component": result.breakdown.keyword_coverage_component,
                "weight_similarity": result.breakdown.weight_similarity,
                "weight_keyword": result.breakdown.weight_keyword,
                "final_score": result.breakdown.final_score,
                # Phase 2 addition (Requirement 3.3 / phase-2 9.5): which
                # algorithm produced the similarity component. A new, optional
                # key only — every Phase 1 field keeps its name and type, and
                # pre-Phase-2 stored rows (where the key is absent) are never
                # rewritten (phase-2 Requirements 6.4, 9.4).
                "similarity_method": result.breakdown.similarity_method,
            },
            matched_keywords=[
                {"term": kw.term, "weight": kw.weight} for kw in result.matched_keywords
            ],
            missing_keywords=[
                {"term": kw.term, "weight": kw.weight} for kw in result.missing_keywords
            ],
            suggestions=[{"keyword": s.keyword, "text": s.text} for s in result.suggestions],
            scorer_version=result.scorer_version,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        session.add(match)
        # Flush so the generated ``id`` is materialised before the audit row
        # references it; the router owns commit (Audit Log §11.3).
        await session.flush()

        # ``match_created`` payload carries internal ids ONLY — never the
        # Restricted ``job_description_text`` (Requirement 8.6, 8.8).
        await self._audit.emit(
            session,
            event_type="match_created",
            user_id=user_id,
            payload={"resume_id": str(resume_id), "match_id": str(match.id)},
        )

        # 6. Best-effort JD Embedding persist (phase-2 Requirements 2.6, 2.12).
        #    Only when the Phase 2 pipeline produced the result — fallback and
        #    Degraded_Mode requests store no Embedding (phase-2 Req 7.2, 7.3).
        #    The ``match_results`` row is already flushed above, so the FK
        #    target exists; a persistence failure rolls back to the savepoint
        #    only and never fails the request.
        if outcome.jd_vector is not None and outcome.pipeline is not None:
            try:
                async with session.begin_nested():
                    await insert_match_embedding(
                        session,
                        match_result_id=match.id,
                        user_id=user_id,
                        vector=outcome.jd_vector,
                        model_name=outcome.pipeline.model_name,
                        model_revision=outcome.pipeline.model_revision,
                    )
            except Exception as exc:  # Best-effort: persistence never fails the request.
                _log.warning(
                    "embedding_persist_failure",
                    match_id=str(match.id),
                    error_type=type(exc).__name__,
                )

        return match

    # ------------------------------------------------------------------
    # Phase 2 scoring + fallback ladder (phase-2 Requirements 2.6-2.9,
    # 2.12, 3.10, 4.10, 4.12, 6.2, 7.2, 7.3, 7.4).
    # ------------------------------------------------------------------

    async def _score_with_fallback(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        resume_id: UUID,
        resume_text: str,
        job_description: str,
    ) -> _ScoreOutcome:
        """Score via the Phase 2 pipeline, falling back per the decision tree.

        Implements the design "Fallback decision tree" exactly. Every rung
        completes the request with the **untouched** Phase 1 engine (via
        ``scorer_adapter.score``, whose result carries the Phase 1
        ``scorer_version`` stamp — phase-2 Requirement 6.2), stores no
        Embedding for the request, and emits exactly one structured event
        per occurrence. Events carry ``request_id`` (bound by the request-id
        middleware into structlog contextvars), internal ids, and — where an
        exception class is informative — ``error_type`` only; never input
        text, vector values, or exception messages that could echo Restricted
        PII (phase-2 Requirements 2.9, 7.4).

        The ladder, in evaluation order:

        * **Degraded_Mode** — no loaded pipeline: Phase 1 engine, **no**
          per-request event (startup already logged ``model_load_failure``
          once; phase-2 Requirements 7.2, 7.6).
        * **``embedding_timeout``** — either embed call exceeded the
          configured wall-clock bound (phase-2 Requirement 2.8).
        * **``embedding_runtime_error``** — any other failure while producing
          the two vectors (encode errors, stored-Embedding read errors).
        * **``embedding_geometry_error``** — the Semantic_Scorer found the
          cosine undefined (phase-2 Requirement 3.10).
        * **``skill_extraction_empty``** — non-empty JD but an empty analyzed
          skill set: fall back to the full Phase 1 engine, whose keyword
          derivation replaces the skill extraction (phase-2 Requirement 4.10,
          design D8).
        * **``skill_extraction_error``** — any other failure inside the
          Phase 2 scoring step (phase-2 Requirement 4.12).

        A fallback here is strictly per-request: no module or process state is
        mutated, so the next request retries the full Phase 2 path and the
        process never flips into Degraded_Mode (phase-2 Requirement 7.3).
        """
        pipeline = get_semantic_pipeline()
        if pipeline is None:
            # Degraded_Mode (phase-2 Req 7.2): Phase 1 engine, no per-request
            # event — the once-at-startup ``model_load_failure`` covers it and
            # per-request noise would violate the one-event-per-occurrence
            # discipline (phase-2 Req 7.4, 7.6).
            return _ScoreOutcome(scorer_adapter.score(resume_text, job_description), None, None)

        timeout_seconds = float(self._settings.embedding_timeout_seconds)

        try:
            resume_vector = await self._resume_vector(
                session,
                pipeline=pipeline,
                user_id=user_id,
                resume_id=resume_id,
                resume_text=resume_text,
                timeout_seconds=timeout_seconds,
            )
            jd_vector = await embed_with_timeout(job_description, timeout_seconds)
        except EmbeddingTimeoutError:
            _log.warning("embedding_timeout", resume_id=str(resume_id))
            return _ScoreOutcome(scorer_adapter.score(resume_text, job_description), None, None)
        except Exception as exc:  # Any other embed-phase failure degrades.
            _log.warning(
                "embedding_runtime_error",
                resume_id=str(resume_id),
                error_type=type(exc).__name__,
            )
            return _ScoreOutcome(scorer_adapter.score(resume_text, job_description), None, None)

        try:
            result = pipeline.scorer.score(resume_text, job_description, resume_vector, jd_vector)
        except EmbeddingGeometryError:
            _log.warning("embedding_geometry_error", resume_id=str(resume_id))
            return _ScoreOutcome(scorer_adapter.score(resume_text, job_description), None, None)
        except EmptyAnalyzedSetError:
            # Non-empty JD, empty analyzed skill set (phase-2 Req 4.10): the
            # Phase 1 engine's keyword derivation takes over (design D8).
            _log.warning("skill_extraction_empty", resume_id=str(resume_id))
            return _ScoreOutcome(scorer_adapter.score(resume_text, job_description), None, None)
        except Exception as exc:  # Any other Phase 2 scoring failure degrades.
            _log.warning(
                "skill_extraction_error",
                resume_id=str(resume_id),
                error_type=type(exc).__name__,
            )
            return _ScoreOutcome(scorer_adapter.score(resume_text, job_description), None, None)

        return _ScoreOutcome(result, jd_vector, pipeline)

    async def _resume_vector(
        self,
        session: AsyncSession,
        *,
        pipeline: SemanticPipeline,
        user_id: UUID,
        resume_id: UUID,
        resume_text: str,
        timeout_seconds: float,
    ) -> list[float]:
        """Return the resume's Embedding: stored-and-current, or regenerated.

        The reuse-vs-regenerate decision (phase-2 Requirement 2.7) is made
        from stored data alone: a stored Embedding is reused only when its
        recorded model name **and** revision equal the loaded pipeline's;
        anything else (missing row, stale identity) regenerates under the
        configured timeout and best-effort upserts the fresh vector (phase-2
        Requirements 2.6, 2.8).

        Upsert semantics mirror ``Resume_Service._embed_resume_best_effort``:
        ``flush`` pushes any pending outer-transaction state first, then the
        upsert runs inside a SAVEPOINT so a DB rejection rolls back to the
        savepoint only — one ``embedding_persist_failure`` event, and the
        request proceeds with the in-memory vector (phase-2 Requirement 2.12;
        persistence failure never fails the request). Embed errors propagate
        to the caller, which categorizes them onto the fallback ladder.
        """
        stored = await get_resume_embedding(session, resume_id=resume_id, user_id=user_id)
        if (
            stored is not None
            and stored.model_name == pipeline.model_name
            and stored.model_revision == pipeline.model_revision
        ):
            return stored.vector

        vector = await embed_with_timeout(resume_text, timeout_seconds)

        # Best-effort upsert (phase-2 Req 2.12): flush the outer transaction,
        # then isolate the write in a savepoint so a rejection (e.g. a
        # wrong-dimension vector) cannot poison the request's transaction.
        await session.flush()
        try:
            async with session.begin_nested():
                await upsert_resume_embedding(
                    session,
                    resume_id=resume_id,
                    user_id=user_id,
                    vector=vector,
                    model_name=pipeline.model_name,
                    model_revision=pipeline.model_revision,
                )
        except Exception as exc:  # Best-effort: persistence never fails the request.
            _log.warning(
                "embedding_persist_failure",
                resume_id=str(resume_id),
                error_type=type(exc).__name__,
            )
        return vector

    # ------------------------------------------------------------------
    # list_matches (Requirement 9.1).
    # ------------------------------------------------------------------

    async def list_matches(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        limit: int,
        cursor: str | None,
    ) -> MatchPage:
        """Return one cursor-paginated page of the caller's non-deleted matches.

        Ordered by ``created_at`` descending with ``id`` descending as the
        deterministic tiebreak, backed by the composite
        ``match_results_user_created_idx`` index (Requirement 9.1). Keyset (not
        offset) pagination: a non-null ``cursor`` selects only rows strictly
        "older" than the last row of the previous page. One extra row beyond
        ``limit`` is fetched to decide whether a ``next_cursor`` is warranted
        without a second COUNT query.

        Every soft-deleted row is excluded, and the query is scoped to
        ``user_id`` so another user's matches never appear (Requirements 1.4,
        9.1).

        Args:
            session: Active request-scoped session.
            user_id: The authenticated principal; the query is scoped to it.
            limit: Page size (the router clamps to 1..100). Values below 1 fall
                back to the default page size so the slice is well-defined.
            cursor: Opaque token from a previous page, or ``None`` for the first
                page. A malformed token is treated as the first page.

        Returns:
            MatchPage: the page's rows and the next-page cursor (``None`` when
            exhausted).
        """
        effective_limit = limit if limit >= 1 else _DEFAULT_LIST_LIMIT

        stmt = (
            select(MatchResult)
            .where(
                MatchResult.user_id == user_id,
                MatchResult.deleted_at.is_(None),
            )
            .order_by(MatchResult.created_at.desc(), MatchResult.id.desc())
        )

        decoded = _decode_cursor(cursor) if cursor else None
        if decoded is not None:
            cursor_created_at, cursor_id = decoded
            # Keyset predicate for ``ORDER BY created_at DESC, id DESC``: the
            # next page is rows whose ``created_at`` is older, or — within the
            # same ``created_at`` — whose ``id`` sorts lower. The row-value
            # (tuple) comparison expresses this directly and matches the
            # composite index ordering; the right-hand side is a plain Python
            # tuple of bound values, which SQLAlchemy renders as a row
            # constructor for the comparison.
            stmt = stmt.where(
                tuple_(MatchResult.created_at, MatchResult.id) < (cursor_created_at, cursor_id)
            )

        # Fetch one extra row to detect whether a further page exists.
        stmt = stmt.limit(effective_limit + 1)
        rows = list((await session.execute(stmt)).scalars().all())

        has_more = len(rows) > effective_limit
        page_rows = rows[:effective_limit]
        next_cursor: str | None = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = _encode_cursor(last.created_at, last.id)

        return MatchPage(items=page_rows, next_cursor=next_cursor)

    # ------------------------------------------------------------------
    # get_match (Requirements 9.3, 9.6).
    # ------------------------------------------------------------------

    async def get_match(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        match_id: UUID,
    ) -> MatchResult:
        """Return one owned, non-deleted Match_Result.

        The query is scoped to ``user_id`` and ``deleted_at IS NULL``; a
        missing, soft-deleted, or other-owner match raises
        :class:`NotFoundError` (404 ``not_found``), so another account's match
        is indistinguishable from one that does not exist (Requirements 1.5,
        1.6, 9.3).

        Crucially, the lookup does **not** join or filter on the referenced
        Resume's ``deleted_at``: a Match_Result is returned even when its Resume
        was later soft-deleted, because the score and analysis are retained
        independently of the Resume's lifecycle (Requirement 9.6).
        """
        result = await session.execute(
            select(MatchResult).where(
                MatchResult.id == match_id,
                MatchResult.user_id == user_id,
                MatchResult.deleted_at.is_(None),
            )
        )
        match = result.scalar_one_or_none()
        if match is None:
            raise NotFoundError("Match not found.")
        return match

    # ------------------------------------------------------------------
    # soft_delete_match (Requirements 9.4, 9.5).
    # ------------------------------------------------------------------

    async def soft_delete_match(
        self,
        session: AsyncSession,
        *,
        user_id: UUID,
        match_id: UUID,
    ) -> None:
        """Soft-delete an owned Match_Result; idempotent.

        For an owned match whose ``deleted_at`` is null, sets ``deleted_at`` to
        now, bumps ``updated_at``, and emits a ``match_deleted`` audit row
        referencing the match id (Requirement 9.4). A match that is already
        soft-deleted is a no-op that emits NO second audit row, so repeated
        deletes are idempotent (Requirement 9.5). The router maps both the
        first and subsequent calls to HTTP 204.

        A match that does not exist or belongs to another user also resolves to
        a silent no-op here: the router returns 204 for the
        already-deleted/non-existent cases uniformly, and per-user scoping
        ensures another account's match is never mutated (Requirement 1.4).
        """
        # Scope strictly to the owner. We deliberately do NOT filter on
        # ``deleted_at`` here so we can distinguish "already deleted" (no audit)
        # from "not present at all" without a second query.
        result = await session.execute(
            select(MatchResult).where(
                MatchResult.id == match_id,
                MatchResult.user_id == user_id,
            )
        )
        match = result.scalar_one_or_none()

        if match is None or match.deleted_at is not None:
            # Not owned / not present, or already soft-deleted: idempotent
            # no-op, no second ``match_deleted`` row (Requirement 9.5).
            return

        now = _now()
        match.deleted_at = now
        match.updated_at = now

        await self._audit.emit(
            session,
            event_type="match_deleted",
            user_id=user_id,
            payload={"match_id": str(match.id)},
        )

    # ------------------------------------------------------------------
    # Internal helpers.
    # ------------------------------------------------------------------

    async def _enforce_scoring_quota(self, session: AsyncSession, *, user_id: UUID) -> None:
        """Raise :class:`QuotaExceededError` when today's Scoring_Quota is met.

        Counts the user's ``match_results`` rows created since 00:00 UTC today
        (design "Quota enforcement" — the count is exact, durable, and
        independent of Redis). The count includes later-soft-deleted rows: the
        scoring work still happened and still cost compute, so it still counts
        against the daily quota. On breach, a ``quota_rejected`` audit row
        naming ``quota="scoring"`` is emitted and the error carries a
        PII-safe ``detail`` stating the limit and the UTC reset instant
        (Requirements 11.5, 11.6).
        """
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

    async def _load_owned_resume(
        self, session: AsyncSession, *, user_id: UUID, resume_id: UUID
    ) -> Resume:
        """Load a Resume scoped to its owner and ``deleted_at IS NULL``.

        Reading ``resumes`` here is a read-only ownership/extractability check
        for match creation, not a write — the ``resumes`` table remains owned by
        the Resume_Service for all mutations (import-boundary table). A missing,
        soft-deleted, or other-owner row raises :class:`NotFoundError` so the
        match endpoint returns the ``not_found`` envelope without disclosing
        another account's resume (Requirement 8.4).
        """
        result = await session.execute(
            select(Resume).where(
                Resume.id == resume_id,
                Resume.user_id == user_id,
                Resume.deleted_at.is_(None),
            )
        )
        resume = result.scalar_one_or_none()
        if resume is None:
            raise NotFoundError("Resume not found.")
        return resume


__all__ = [
    "MatchPage",
    "Scoring_Service",
]
