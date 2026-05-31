"use client";

import Link from "next/link";
import * as React from "react";

import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";

import {
  MatchListResponseSchema,
  ResumeListResponseSchema,
} from "@matchlayer/shared-types";

/**
 * Library_View — `(app)/library` (Requirement 13.8; design "frontend
 * components" → `(app)/library/page.tsx`).
 *
 * Lists the signed-in User_Account's resumes and recent Match_Results inside
 * the Authenticated_Shell. Each match links to its Results_Page at
 * `/matches/{id}`. Data comes from the two cursor-paginated list endpoints:
 *
 *   - `GET /api/v1/resumes` → `ResumeListResponse` ({ items, next_cursor })
 *   - `GET /api/v1/matches` → `MatchListResponse` ({ items, next_cursor })
 *
 * Both are fetched through `apiFetch` (Bearer attach + silent refresh-and-retry)
 * and validated at the boundary with the generated Zod schemas from
 * `@matchlayer/shared-types` (no hand-written API types, per `conventions.md`).
 * The two requests are independent: one failing still lets the other section
 * render, so a transient match-list error never hides the resume list.
 *
 * This must be a Client Component — it fetches with the in-memory access token
 * and tracks per-section load state. The surrounding `(app)` shell gates the
 * session server-side and exports `robots: { index: false, follow: false }`, so
 * this page inherits `noindex, nofollow` (Requirement 15.2) and adds no
 * discoverability metadata (Requirement 15.1).
 *
 * Privacy (`security.md`): `original_filename` is part of the API's safe
 * response field set and is shown so users can recognize their own resume, but
 * it is never logged. Nothing PII-derived is written to the console or placed
 * anywhere outside the rendered, auto-escaped JSX text nodes; no
 * `dangerouslySetInnerHTML` is used.
 */

/** Resume list item, inferred from the generated schema so the component never
 *  hand-writes the API response type. */
type Resume = ReturnType<
  typeof ResumeListResponseSchema.parse
>["items"][number];

/** Recent-match list item (omits `job_description_text` by contract), inferred
 *  from the generated schema. */
type MatchItem = ReturnType<
  typeof MatchListResponseSchema.parse
>["items"][number];

/** The request lifecycle states each section renders distinct UI for. */
type LoadState = "loading" | "ready" | "error";

/** A loaded, validated list page: the items plus the opaque next cursor. */
interface ListPage<T> {
  items: T[];
  nextCursor: string | null;
}

export default function LibraryPage(): React.JSX.Element {
  const [resumesState, setResumesState] = React.useState<LoadState>("loading");
  const [resumes, setResumes] = React.useState<ListPage<Resume> | null>(null);

  const [matchesState, setMatchesState] = React.useState<LoadState>("loading");
  const [matches, setMatches] = React.useState<ListPage<MatchItem> | null>(
    null,
  );

  React.useEffect(() => {
    let cancelled = false;

    async function load(): Promise<void> {
      // Fetch both lists concurrently but settle them independently so a
      // failure in one never blanks the other.
      const [resumePage, matchPage] = await Promise.all([
        fetchResumes(),
        fetchMatches(),
      ]);

      if (cancelled) return;

      if (resumePage === null) {
        setResumesState("error");
      } else {
        setResumes(resumePage);
        setResumesState("ready");
      }

      if (matchPage === null) {
        setMatchesState("error");
      } else {
        setMatches(matchPage);
        setMatchesState("ready");
      }
    }

    void load();

    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="mx-auto max-w-5xl space-y-12">
      <header className="flex flex-wrap items-center justify-between gap-4">
        <div className="space-y-1">
          <h1 className="text-2xl font-semibold tracking-tight text-text">
            Your library
          </h1>
          <p className="text-text-muted">
            Your uploaded resumes and recent match results.
          </p>
        </div>
        <Button asChild>
          <Link href="/upload">New match</Link>
        </Button>
      </header>

      <ResumesSection state={resumesState} page={resumes} />
      <MatchesSection state={matchesState} page={matches} />
    </div>
  );
}

/**
 * Fetch the resume list and validate it against the generated
 * `ResumeListResponseSchema`. Returns the normalized page on success, or `null`
 * on any failure (network error, non-2xx, unparseable body, or contract drift)
 * so the caller can show a friendly per-section error rather than a thrown
 * stack.
 */
async function fetchResumes(): Promise<ListPage<Resume> | null> {
  const body = await fetchJson("/api/v1/resumes");
  if (body === undefined) {
    return null;
  }
  const parsed = ResumeListResponseSchema.safeParse(body);
  if (!parsed.success) {
    return null;
  }
  return {
    items: parsed.data.items,
    nextCursor: parsed.data.next_cursor ?? null,
  };
}

/**
 * Fetch the recent-match list and validate it against the generated
 * `MatchListResponseSchema`. Same failure contract as {@link fetchResumes}.
 */
async function fetchMatches(): Promise<ListPage<MatchItem> | null> {
  const body = await fetchJson("/api/v1/matches");
  if (body === undefined) {
    return null;
  }
  const parsed = MatchListResponseSchema.safeParse(body);
  if (!parsed.success) {
    return null;
  }
  return {
    items: parsed.data.items,
    nextCursor: parsed.data.next_cursor ?? null,
  };
}

/**
 * Issue a GET via `apiFetch` (Bearer attach + silent refresh-and-retry) and
 * return the parsed JSON body, or `undefined` on any failure. `undefined` (not
 * `null`) signals failure because a JSON body of `null` is itself a valid —
 * though here unexpected — parse result.
 */
async function fetchJson(path: string): Promise<unknown> {
  let res: Response;
  try {
    res = await apiFetch(path);
  } catch {
    return undefined;
  }
  if (!res.ok) {
    return undefined;
  }
  try {
    return await res.json();
  } catch {
    return undefined;
  }
}

/**
 * The "Resumes" section: a heading, then either a skeleton, a friendly error,
 * an empty-state prompt, or the resume rows.
 */
function ResumesSection({
  state,
  page,
}: {
  state: LoadState;
  page: ListPage<Resume> | null;
}): React.JSX.Element {
  return (
    <section aria-labelledby="resumes-heading" className="space-y-4">
      <h2 id="resumes-heading" className="text-lg font-semibold text-text">
        Resumes
      </h2>

      {state === "loading" ? (
        <ListSkeleton />
      ) : state === "error" || page === null ? (
        <SectionError body="We couldn't load your resumes right now. Please try again in a moment." />
      ) : page.items.length === 0 ? (
        <EmptyState
          body="You haven't uploaded a resume yet."
          ctaLabel="Upload a resume"
        />
      ) : (
        <>
          <ul className="space-y-3">
            {page.items.map((resume) => (
              <li key={resume.id}>
                <ResumeRow resume={resume} />
              </li>
            ))}
          </ul>
          {page.nextCursor !== null ? (
            <p className="text-xs text-text-subtle">
              Showing your most recent resumes.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

/**
 * The "Recent matches" section. Each row links to its Results_Page at
 * `/matches/{id}` (Requirement 13.8).
 */
function MatchesSection({
  state,
  page,
}: {
  state: LoadState;
  page: ListPage<MatchItem> | null;
}): React.JSX.Element {
  return (
    <section aria-labelledby="matches-heading" className="space-y-4">
      <h2 id="matches-heading" className="text-lg font-semibold text-text">
        Recent matches
      </h2>

      {state === "loading" ? (
        <ListSkeleton />
      ) : state === "error" || page === null ? (
        <SectionError body="We couldn't load your matches right now. Please try again in a moment." />
      ) : page.items.length === 0 ? (
        <EmptyState
          body="You haven't run a match yet."
          ctaLabel="Run your first match"
        />
      ) : (
        <>
          <ul className="space-y-3">
            {page.items.map((match) => (
              <li key={match.id}>
                <MatchRow match={match} />
              </li>
            ))}
          </ul>
          {page.nextCursor !== null ? (
            <p className="text-xs text-text-subtle">
              Showing your most recent matches.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}

/**
 * One resume row: the original filename (plain text, for the user to identify
 * their own file) and its extraction status badge. Not a link — Phase 1 has no
 * per-resume detail page.
 */
function ResumeRow({ resume }: { resume: Resume }): React.JSX.Element {
  return (
    <div className="flex items-center justify-between gap-4 rounded-xl border border-border bg-bg-elevated p-4 shadow-sm">
      <div className="min-w-0 space-y-1">
        <p className="truncate text-sm font-medium text-text">
          {resume.original_filename}
        </p>
        <p className="text-xs text-text-muted">
          Uploaded <FormattedTime iso={resume.created_at} />
        </p>
      </div>
      <ExtractionStatusBadge status={resume.extraction_status} />
    </div>
  );
}

/**
 * One match row, rendered as a full-row `Link` to its Results_Page. Shows the
 * score (mono + tabular-nums per `design.md`) and a human-readable timestamp.
 * Carries a visible, branded focus ring (`design.md` a11y: "visible, branded
 * focus rings").
 */
function MatchRow({ match }: { match: MatchItem }): React.JSX.Element {
  return (
    <Link
      href={`/matches/${encodeURIComponent(match.id)}`}
      className={cn(
        "flex items-center justify-between gap-4 rounded-xl border border-border bg-bg-elevated p-4 shadow-sm transition-colors",
        "hover:border-border-strong",
        "outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
      )}
    >
      <div className="min-w-0 space-y-1">
        <p className="text-sm font-medium text-text">Match result</p>
        <p className="text-xs text-text-muted">
          <FormattedTime iso={match.created_at} />
        </p>
      </div>
      <div className="flex items-baseline gap-1 shrink-0">
        <span className="font-mono text-lg font-semibold tabular-nums text-text">
          {match.score}
        </span>
        <span className="font-mono text-xs tabular-nums text-text-muted">
          /100
        </span>
      </div>
    </Link>
  );
}

/**
 * Extraction status badge in a token family matched to the status: `succeeded`
 * → success, `pending` → neutral/muted, `failed` → danger. Plain text inside a
 * pill; contrast holds in both themes.
 */
function ExtractionStatusBadge({
  status,
}: {
  status: Resume["extraction_status"];
}): React.JSX.Element {
  const toneClass =
    status === "succeeded"
      ? "border-success/30 bg-success/10 text-success"
      : status === "failed"
        ? "border-danger/30 bg-danger/10 text-danger"
        : "border-border-strong bg-bg text-text-muted";

  const label =
    status === "succeeded"
      ? "Ready"
      : status === "failed"
        ? "Failed"
        : "Processing";

  return (
    <span
      className={cn(
        "shrink-0 rounded-full border px-3 py-1 text-xs font-medium",
        toneClass,
      )}
    >
      {label}
    </span>
  );
}

/**
 * Render an ISO 8601 timestamp as a human-readable, locale-formatted string
 * inside a semantic `<time>` element. A malformed value collapses to the raw
 * string rather than rendering "Invalid Date".
 */
function FormattedTime({ iso }: { iso: string }): React.JSX.Element {
  const date = new Date(iso);
  const readable = Number.isNaN(date.getTime())
    ? iso
    : date.toLocaleString(undefined, {
        dateStyle: "medium",
        timeStyle: "short",
      });
  return <time dateTime={iso}>{readable}</time>;
}

/**
 * Friendly empty-state card with a prompt linking to `/upload` — used when a
 * section has loaded successfully but has no rows yet.
 */
function EmptyState({
  body,
  ctaLabel,
}: {
  body: string;
  ctaLabel: string;
}): React.JSX.Element {
  return (
    <div className="space-y-4 rounded-xl border border-dashed border-border-strong bg-bg-elevated p-8 text-center">
      <p className="text-sm text-text-muted">{body}</p>
      <Button asChild variant="outline">
        <Link href="/upload">{ctaLabel}</Link>
      </Button>
    </div>
  );
}

/**
 * Friendly per-section error surface — never a raw error object or stack trace.
 */
function SectionError({ body }: { body: string }): React.JSX.Element {
  return (
    <div className="rounded-xl border border-border bg-bg-elevated p-6 text-center">
      <p className="text-sm text-text-muted">{body}</p>
    </div>
  );
}

/**
 * Loading skeleton that mirrors the resolved list shape (a stack of rows) per
 * `design.md` — skeletons over a bare spinner. Purely presentational and
 * `aria-hidden`; the section announces nothing while the request is in flight.
 */
function ListSkeleton(): React.JSX.Element {
  return (
    <div aria-hidden="true" className="animate-pulse space-y-3">
      {[0, 1, 2].map((row) => (
        <div
          key={row}
          className="h-16 rounded-xl border border-border bg-bg-elevated"
        />
      ))}
    </div>
  );
}
