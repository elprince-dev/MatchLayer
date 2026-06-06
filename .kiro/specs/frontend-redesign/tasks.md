# Implementation Plan: Frontend Redesign

## Overview

This plan implements the four MVP screens of MatchLayer against the **approved** design document.
The design IS the mandatory design-review-gate artifact required by Requirement 22 and is approved,
so implementation is now permitted — **no task re-creates wireframes or mockups** (Req 22.6); the
Section 8 layout specs are implemented directly and verified by the Section 9/10 gates.

Work proceeds **flagship-first** (Req 14.8). The build order is:

1. **Foundation** — Playwright harness, design tokens, theme defaults, generated types, shadcn primitives, `(marketing)` group + sitemap.
2. **Shared cross-screen components** — MotionSafe, SkeletonLoader, ErrorState, SkipNav, score-label, fixtures.
3. **ATS Results (flagship)** — components, then wiring (`/matches/[id]`), then a flagship checkpoint.
4. **Upload** — the second half of the core authenticated flow (Results + Upload).
5. **Auth** — `/login`, `/register`.
6. **Landing** — public marketing page.
7. **Cross-cutting validation** — Playwright screenshot/layout gates and axe-core + computed-contrast
   accessibility verification across all four screens, in both themes.

Upload is built **before** Auth/Landing because Results + Upload form the core authenticated flow;
Landing/Auth polish comes after the flagship is functionally complete. Per the flagship-first
priority (Req 14.8), the flagship's dedicated visual/layout acceptance gates are the **highest-priority**
checks within the consolidated validation phase and are listed first there (task 9.1).

Stack and constraints (from the design + steering):

- **Language/framework:** TypeScript (strict: `noImplicitAny`, `strictNullChecks`, `noUncheckedIndexedAccess`)
  - Next.js App Router + React function components. The design uses a concrete stack, **not pseudocode**,
    so no implementation-language selection is required.
- **Backend contract is fixed (Req 20):** all data types are imported from `packages/shared-types`
  (generated from the FastAPI OpenAPI spec). No task introduces suggestion `title`/`priority`, a third
  score dimension, or `job_description_text`. Components wire only to the real fields —
  `MatchResponse` / `ScoreBreakdown` (two components) / `Keyword{term,weight}` / `Suggestion{keyword,text}` /
  `ResumeResponse{extraction_status}`.
- **Styling:** token-only Tailwind v4 (`globals.css` custom properties) — no hex, no inline styles, no
  arbitrary bracket color utilities; Framer Motion as the sole animation library; Lucide icons;
  `next-themes` (dark default, no System).
- **Testing (design Section 9, 10, Testing Strategy):** Vitest + Testing Library unit tests (co-located
  with the components that have logic), axe-core + computed-contrast accessibility checks in both themes,
  Playwright visual/layout acceptance gates, and integration tests using the Section 5 fixtures.
  **This is not a property-based-testing feature** — the design's Testing Strategy establishes this
  explicitly, so there are **no PBT / correctness-property tasks**.
- **Routes & security preserved (Req 21.10):** existing routes (`/`, `/login`, `/register`, `/upload`,
  `/matches/[id]`) and the security-headers `proxy.ts` are preserved; the `(marketing)` group + `sitemap.ts`
  (public routes only) are added; `(app)`/`(auth)` stay noindex. The legacy `(app)/dashboard` and
  `(app)/library` folders and `(auth)/forgot-password`/`reset-password` are **out of scope** — not
  designed, linked, or expanded.

## Tasks

- [x] 1. Foundation — tooling, tokens, theme, codegen, primitives, routing
  - [x] 1.1 Add Playwright (design Step 0) as the visual/E2E harness
    - Add `@playwright/test` as a `devDependency` of `apps/web` (Playwright is currently **only** an optional `next` peer in `pnpm-lock.yaml`, not installed).
    - Create `apps/web/playwright.config.ts` with the three viewport projects from design Section 9.2 — `desktop-1280` (1280×720), `desktop-1440` (1440×900), `mobile-390` (390×844) — plus a 1920×1080 assert-only context; set `testDir` to `apps/web/tests/visual/`.
    - Add a `test:visual` script to `apps/web/package.json`. Do **not** author screen specs yet (they land in the validation phase, tasks 9.1–9.2).
    - _Requirements: 14.4, 18.1, 18.5; design Section 9.1, 9.2_

  - [x] 1.2 Finalize design tokens in `globals.css` (radius/shadow/motion/reduced-motion)
    - In `apps/web/src/app/globals.css`, add the `--radius-card/-hero/-pill`, `--shadow-resting/-elevated`, and `--motion-micro/-layout/-hero/-ease` custom properties on `:root` (theme-invariant), exactly per design Section 4.6–4.9. Colors already exist — leave the existing color/font triplets untouched.
    - Add the `@media (prefers-reduced-motion: reduce)` block re-pointing `--motion-micro/-layout/-hero` to `0ms`.
    - Re-export the radius/shadow tokens through the existing `@theme inline` block so Tailwind utilities resolve them.
    - _Requirements: 1.4, 1.5, 1.9; design Section 4_

  - [x] 1.3 Update `theme-provider.tsx` to the required theme defaults (delta from current code)
    - Change `apps/web/src/components/theme-provider.tsx` from `defaultTheme="system"` + `enableSystem` to `defaultTheme="dark"`, `enableSystem={false}`, keeping `attribute="class"` and `disableTransitionOnChange`.
    - Verify `app/layout.tsx` keeps `<html suppressHydrationWarning>` so the `next-themes` pre-paint script applies the resolved theme (stored preference, else dark) before first paint.
    - _Requirements: 2.1, 2.2, 2.4, 2.6, 2.7, 21.5; design Section 6.4_

  - [x] 1.4 Generate and verify shared types from the OpenAPI contract
    - Run `pnpm codegen` to (re)generate `packages/shared-types` from the FastAPI OpenAPI schema; confirm curated re-exports exist for `MatchResponse`, `ScoreBreakdown`, `Keyword`, `Suggestion`, `ResumeResponse`.
    - Confirm the generated contract has **no** `title`/`priority` on `Suggestion`, **no** third score dimension, and **no** `job_description_text`; if drift exists, treat the generated types as authoritative (Req 20.7).
    - _Requirements: 20.1, 20.3, 20.5, 20.7, 21.11; design Section 6.6, Data Models_

  - [x] 1.5 Add the shadcn UI primitives styled via tokens
    - Copy in `apps/web/src/components/ui/{input,textarea,label,progress,skeleton}.tsx` (New York style, `cn()` from `lib/utils`), each accepting `className` and styled exclusively via tokens (no hex/inline). Add the required Radix deps (`@radix-ui/react-label`, `@radix-ui/react-progress`) to `apps/web/package.json`. Reuse the existing `button.tsx`.
    - Apply the **2px branded focus ring** (`ring-2 ring-brand` + offset, ≥3:1 contrast) to every focusable primitive; never `outline:none` without a replacement.
    - _Requirements: 16.8, 19.2, 21.2, 21.3; design Section 7.3, Section 10.3_

  - [x] 1.6 Add the `(marketing)` route group and `app/sitemap.ts`; preserve routing/proxy
    - Create `apps/web/src/app/(marketing)/layout.tsx` (Server Component) wiring shared SEO helpers under `apps/web/src/lib/seo/` (Metadata API only, per seo.md). **Do not create `(marketing)/page.tsx` yet** — that would collide with the still-present legacy `app/page.tsx` at `/`. The marketing page is built and the legacy page removed together in task 8.7.
    - Create `apps/web/src/app/sitemap.ts` listing **public routes only** (`/`); it must never list `(app)`/`(auth)`/`/api/` paths. Leave `app/robots.ts` disallow rules and `src/proxy.ts` (security headers / `X-Robots-Tag`) intact (Req 21.10).
    - _Requirements: 7.5, 8.9, 8.10, 21.7, 21.9, 21.10; design Section 6.2; seo.md_

  - [x] 1.7 Tests for foundation wiring
    - Unit-test the theme-provider config (dark default, `enableSystem` false); assert `globals.css` declares the new radius/shadow/motion tokens and the reduced-motion override.
    - **Preserve** the existing `apps/web/tests/non-indexing.test.ts` and add sitemap assertions: `app/sitemap.ts` contains `/` and **none** of `/upload`, `/matches`, `/login`, `/register`, `/api/`.
    - _Requirements: 1.4, 1.9, 2.7, 7.5, 8.9; design Section 9, Section 11_

- [x] 2. Shared cross-screen components
  - [x] 2.1 Add the `MotionSafe` wrapper alongside the existing hook
    - Extend `apps/web/src/components/motion-safe.tsx` with a `MotionSafe` wrapper built on the existing `useMotionSafeProps`, giving animated components one chokepoint that forces `animate=initial` + `transition={duration:0}` under `prefers-reduced-motion`. Loading/progress and focus-ring transitions are exempt.
    - _Requirements: 1.9, 15.4, 15.6, 16.11, 19.6; design Section 6.5, Section 7.3 (MotionSafe)_

  - [x] 2.2 Build the `SkeletonLoader` compositions
    - Create `apps/web/src/components/skeleton-loader.tsx` with `variant: "results" | "upload"`, composing `ui/skeleton.tsx`: `results` = gauge circle + two bar placeholders + pill rows matching the success layout shape; `upload` = drop-zone + field placeholders. Shimmer cycles every 1.5s. This is the primary loading pattern — **no spinner-only loading**.
    - _Requirements: 10.5, 13.4, 16.6, 17.1, 17.2; design Section 7.3 (SkeletonLoader)_

  - [x] 2.3 Build the shared `ErrorState`
    - Create `apps/web/src/components/error-state.tsx` with props `{ title; message; action?; secondaryHref? }`. Title ≤60 chars, message ≤200 chars plain-language (no jargon), ≥1 recovery action. Use the `danger` token for the **indicator only**; explanatory text uses `text`/`text-muted`. Never render error codes, stack traces, internal ids, or RFC 7807 `type`/`request_id`.
    - _Requirements: 16.6, 17.3, 17.4, 17.5; design Section 7.3 (ErrorState), Error Handling, Section 10.2_

  - [x] 2.4 Add a skip-navigation link and wire it + landmarks into the layouts
    - Create a `SkipNav` component (first focusable element) that moves focus to the `<main>` landmark; wire it as the first child in `(marketing)/layout.tsx`, `(app)/layout.tsx`, and `(auth)/layout.tsx`. Ensure each layout exposes a `<main id>` target and the `header/nav/main/footer` landmark structure.
    - _Requirements: 19.5, 19.8; design Section 10.3, 10.4_

  - [x] 2.5 Add the score → qualitative label mapping
    - Create `apps/web/src/lib/score-label.ts` mapping score → "Excellent" (80–100), "Good" (60–79), "Fair" (40–59), "Needs Work" (0–39).
    - _Requirements: 10.4, 12.1; design Section 7.1 (ScoreLabel), Testing Strategy_

  - [x] 2.6 Create the Section 5 fixture data
    - Create `apps/web/src/components/results/__fixtures__/match-fixtures.ts` exporting the three `ResumeResponse` fixtures (succeeded / pending / failed) and the three `MatchResponse` fixtures: strong (~85), partial (52), and degenerate (0/0, affirmative-only suggestion). Values must conform **exactly** to the generated types — no invented fields (no suggestion `title`/`priority`, no third dimension, no `job_description_text`). Imported by component, integration, and visual tests.
    - _Requirements: 20.2; design Section 5_

  - [x] 2.7 Unit tests for shared logic and components
    - `score-label` boundary tests (0, 39, 40, 59, 60, 79, 80, 100); `ErrorState` renders mapped copy only and never leaks codes/`request_id`/stack traces; `SkeletonLoader` renders the expected `results`/`upload` shapes.
    - _Requirements: 10.4, 17.1, 17.3, 17.4; design Testing Strategy_

- [x] 3. ATS Results (flagship) — components
  - [x] 3.1 Build `ScoreGauge` + co-located `ScoreLabel`
    - Create `apps/web/src/components/results/score-gauge.tsx` (`'use client'`): circular SVG gauge whose stroke fills clockwise 0→`score`% (0 = empty, 100 = full ring), Signature_Gradient stroke over a `bg-elevated` well; score number in Geist Mono `text-6xl` with gradient text-clip and `tabular-nums`; count-up 0→`score` over **600ms** with easing `[0.16,1,0.3,1]`. Under reduced motion (via `MotionSafe`), render the final score + filled stroke instantly. Render `ScoreLabel` (using `lib/score-label.ts`) directly below. Mobile: gauge ≥120px diameter, score ≥24px. Consume only `MatchResponse.score`.
    - States: animating count-up; reduced-motion final-state; (loading handled by sibling `SkeletonLoader`).
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.6, 10.7, 12.1, 16.1, 18.5; design Section 7.1 (ScoreGauge/ScoreLabel)_

  - [x] 3.2 Build `ScoreBreakdownCard`
    - Create `apps/web/src/components/results/score-breakdown-card.tsx`: **exactly two** labeled progress bars — "TF-IDF similarity" (`similarity_component`) and "Keyword coverage" (`keyword_coverage_component`) — each scaled `[0,1]`→0–100% with `tabular-nums`, showing its associated weight (`weight_similarity` / `weight_keyword`), plus a one-line "final = weighted sum" explainer using `final_score`. Render **no third dimension**. Consume `ScoreBreakdown` from shared-types.
    - Responsive: full-width stacked bars; right column ≥1024px, below the gauge <1024px.
    - _Requirements: 11.1, 11.2, 16.2; design Section 7.1 (ScoreBreakdownCard)_

  - [x] 3.3 Build `KeywordTag` and `KeywordSection` (with light-mode pill mitigation)
    - `keyword-tag.tsx`: `rounded-full` pill, uniform height, `variant: "success" | "warning"`. Per design Section 10.2, pills use a **tinted background fill** (token at low opacity, e.g. `/15`) + a full-token **border** + label in **`text` (primary color)** — **not** colored text — so they pass light-mode AA. Consume `Keyword{term,weight}` (`weight` optional for tooltip; parent owns ordering).
    - `keyword-section.tsx`: heading + wrapped tag list (`gap-2`, never causes horizontal scroll), rendering keywords in received (weight-desc) order; when the array is empty, render a defined `emptyMessage` (distinct copy for matched vs missing) using neutral/success/warning tokens, **never `danger`**.
    - _Requirements: 11.3, 11.4, 11.6, 12.2, 12.3, 12.7, 16.4; design Section 7.1 (KeywordTag/KeywordSection), Section 10.2_

  - [x] 3.4 Build `SuggestionCard`
    - Create `apps/web/src/components/results/suggestion-card.tsx`: renders `Suggestion.text`; associates a small keyword label only when `keyword` is non-empty. **No `title`/`priority` props** (backend supplies neither). The single affirmative suggestion (empty `keyword`) renders in a positive/success style with no missing-keyword label. Consume `Suggestion{keyword,text}`.
    - States: improvement (keyword present); affirmative (keyword empty); participates in the parent's staggered entrance.
    - _Requirements: 11.5, 12.4, 16.5, 20.3; design Section 7.1 (SuggestionCard)_

  - [x] 3.5 Build `EmptyResultState`
    - Create `apps/web/src/components/results/empty-result-state.tsx`: shown when `score_breakdown.similarity_component === 0 && keyword_coverage_component === 0`. Explains not enough readable content was available and offers an "Analyze another job" action → `/upload`. Uses neutral/success/warning tokens, **never `danger`** — it is a valid result, not an error.
    - _Requirements: 12.5, 12.6, 12.7; design Section 7.1 (EmptyResultState)_

  - [x] 3.6 Unit tests for ATS Results components
    - Gauge: reduced-motion renders final state immediately; mobile sizing thresholds. Breakdown: renders exactly two bars + the weights, never a third dimension. KeywordTag: tinted-fill + primary-text treatment (no colored-text reliance). KeywordSection: empty arrays render the defined messages, not `danger`. SuggestionCard: no `title`/`priority`; affirmative variant has no keyword label. Uses the Section 5 fixtures.
    - _Requirements: 10.6, 11.1, 11.5, 12.2, 12.3, 12.4, 18.5; design Testing Strategy_

- [x] 4. ATS Results (flagship) — wiring
  - [x] 4.1 Wire `results-view.tsx` and the `matches/[id]` page
    - Create `apps/web/src/components/results/results-view.tsx` (`'use client'`): TanStack Query (`queryKey: ["match", id]`) over `apiFetch(\`/api/v1/matches/${id}\`)`, parsed with the generated Zod schema. Map states: pending → `SkeletonLoader variant="results"`; 5xx / network / no-response-≤10s (and the 30s general ceiling) → `ErrorState`with **[Retry]** (re-runs the query in place, no navigation) +`/upload`link; 404 → **non-enumerable**`ErrorState`+`/upload`link; degenerate 0/0 →`EmptyResultState`; success → results content composing `ScoreGauge`, `ScoreBreakdownCard`, two `KeywordSection`s (matched=success, missing=warning), staggered `SuggestionCard`s (400ms total, 100ms delay), the `scorer_version` footnote (`text-subtle font-mono`), and the "Analyze another job" primary CTA → `/upload`. **Never render `job_description_text`.** Add an `aria-live="polite"` region announcing result completion.
    - Update `apps/web/src/app/(app)/matches/[id]/page.tsx` to a Server Component shell that reads the `id` param and renders `results-view`; two-column desktop composition (gauge+label left, breakdown right) within `max-w-7xl px-8`, single column on mobile. Confirm the route stays noindex (inherited from the `(app)` layout). No dashboard/history/analytics nav (Req 13.2).
    - _Requirements: 11.7, 11.8, 12.5, 12.6, 13.1, 13.2, 13.3, 13.4, 13.5, 13.6, 17.6, 17.7, 19.4, 21.7, 21.10, 21.11; design Section 6.3, 6.6, Section 8.1, Error Handling_

  - [x] 4.2 Integration tests for the Results data view (Section 5 fixtures)
    - With `apiFetch`/MSW mocked: fixture A (85) renders gauge/label/breakdown/keywords/suggestions; fixture B (52) renders correctly; fixture C (0/0) renders `EmptyResultState` (not an error); loading shows the skeleton; 5xx/network/timeout shows `ErrorState`+retry; 404 shows non-enumerable copy + `/upload` link; `job_description_text` appears nowhere in DOM or payload. Update the existing `apps/web/tests/results-page.test.tsx`.
    - _Requirements: 11.8, 12.5, 12.6, 13.4, 13.5, 13.6, 20.5; design Section 5, Error Handling_

- [x] 5. Checkpoint — flagship functionally complete
  - Ensure all flagship tests pass (ATS Results unit + integration). The flagship is the highest visual-design priority (Req 14.8); its dedicated screenshot/layout acceptance gates run in the consolidated validation phase and are the **first and highest-priority** checks there (task 9.1). Ensure all tests pass, ask the user if questions arise.

- [x] 6. Upload page (`/upload`) — second half of the core authenticated flow
  - [x] 6.1 Build `ProgressBar`
    - Create `apps/web/src/components/upload/progress-bar.tsx` composing `ui/progress.tsx`: determinate animated bar + numeric percentage (0–100, `tabular-nums`). Exempt from reduced-motion suppression as a progress indicator.
    - _Requirements: 9.5, 17.2; design Section 7.2 (ProgressBar)_

  - [x] 6.2 Build `FilePreviewCard` and a byte-size formatter
    - Create `apps/web/src/components/upload/file-preview-card.tsx`: filename, human-readable size (KB/MB), Lucide file-type icon (derived from `content_type`), and a remove button; shown only for valid files. Add a `formatBytes` helper to `apps/web/src/lib/utils.ts`. Consume `ResumeResponse.original_filename`/`byte_size`/`content_type`.
    - _Requirements: 9.3, 9.10, 16.3; design Section 7.2 (FilePreviewCard)_

  - [x] 6.3 Build `UploadWidget`
    - Create `apps/web/src/components/upload/upload-widget.tsx` (`'use client'`): drag-drop zone accepting a single PDF/DOCX ≤5MB + click-to-browse; drag-over highlight (brand border, bg tint, instructional text); posts to `POST /api/v1/resumes` with an `Idempotency-Key` and shows `ProgressBar` during transmission; reflects `extraction_status` (pending → processing; failed → inline `ErrorState` to try another file; succeeded → ready); invalid type/oversize → inline `ErrorState` naming the violated constraint (PDF/DOCX; ≤5MB) with **no** preview card; transmission failure → inline `ErrorState` with retry/remove; remove clears the preview. Calls `onResumeReady(resume)` only when `extraction_status === "succeeded"`. Consume `ResumeResponse`.
    - States: idle, drag-over, uploading, pending, failed, succeeded, invalid file, transmission error.
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.6, 9.7, 9.10, 16.3; design Section 7.2 (UploadWidget)_

  - [x] 6.4 Assemble the Upload page
    - Rebuild `apps/web/src/app/(app)/upload/page.tsx` (`max-w-3xl`, centered): heading + guidance, `UploadWidget`, a JD `Textarea` (ui primitive) with placeholder/guidance and a live character count (`tabular-nums`) enforcing the trimmed 30–50,000 bounds; enable "Analyze Match" only when a resume is `succeeded` **and** 30 ≤ trimmed JD ≤ 50,000, disabled otherwise and on remove; submitting state disables the button and shows "Analyzing your resume…"; on `POST /api/v1/matches` success navigate to `/matches/[id]` using the returned `id`. Confirm the route stays noindex.
    - _Requirements: 9.8, 9.9, 9.11, 9.12, 9.13, 21.7, 21.11; design Section 8.4_

  - [x] 6.5 Unit tests for Upload
    - File-type/size validation (rejects non-PDF/DOCX and >5MB with no preview); submit gating across resume status × JD bounds; `extraction_status` pending/failed/succeeded states; remove disables submit; success navigation target. `formatBytes` edge tests (0, 1023, 1024, 5MB boundary). Update the existing `apps/web/tests/upload-page.test.tsx`.
    - _Requirements: 9.4, 9.6, 9.9, 9.10, 9.12; design Testing Strategy_

- [x] 7. Authentication pages (`/login`, `/register`)
  - [x] 7.1 Rebuild `/login` and add aria-live to the form error
    - Rebuild `apps/web/src/app/(auth)/login/page.tsx` using the existing `AuthCard` (max-w-md, brand mark top): email + password fields (`Input`/`Label`), full-width submit ≥44px, login↔register switch link, privacy/terms trust links. Update `apps/web/src/components/auth/form-error.tsx` to announce inline field errors via `aria-live="polite"` adjacent to the field (validate email format, password ≥12). On failed submit show a **non-enumerable** banner above the form (identical wording for "user not found"/"wrong password") and preserve the entered email; on success navigate to `/upload`. Use React Hook Form + Zod resolver.
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.11, 8.12, 19.4; design Section 8.3_

  - [x] 7.2 Rebuild `/register`
    - Rebuild `apps/web/src/app/(auth)/register/page.tsx` reusing the same card chrome and the updated `form-error`: email + password + confirm-password, validating email format, password ≥12, and confirm-match; non-enumerable failure handling; success → `/upload`.
    - _Requirements: 8.1, 8.3, 8.4, 8.11, 8.12, 19.4; design Section 8.3_

  - [x] 7.3 Add the subtle animated auth background and confirm non-indexing
    - In `apps/web/src/app/(auth)/layout.tsx`, add a subtle gradient-mesh/noise background that never overlays the card and is disabled under `prefers-reduced-motion`; keep the card centered both axes from 320px to ≥1920px. Confirm the layout exports `robots: { index: false, follow: false }` and that `X-Robots-Tag: noindex, nofollow` is set per the route classification.
    - _Requirements: 8.5, 8.6, 8.7, 8.8; design Section 8.3; seo.md, security.md_

  - [x] 7.4 Unit tests for Auth flows
    - Validation (email / password length / confirm-match); the non-enumerable error wording is identical for both failure modes and the entered email is preserved; success navigates to `/upload`. Update/preserve the existing `apps/web/tests/login-form.test.tsx`.
    - _Requirements: 8.4, 8.11, 8.12; design Testing Strategy_

- [x] 8. Landing page (`/`)
  - [x] 8.1 Build `GlassNav`
    - Create `apps/web/src/components/landing/glass-nav.tsx` (`'use client'`): fixed top; brand mark, in-page links (Features, How It Works, About), "Sign in" ghost → `/login`, "Get started" primary → `/register`, and the existing `ThemeToggle`. Transparent over the hero → `bg-glass` (backdrop-blur 12px; 65% light / 55% dark) within 200ms when scrolled past the hero, and back. **No "Pricing"** or any non-MVP link. Mobile (<768px): hamburger with `aria-expanded`.
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6; design Section 7.3 (GlassNav), Section 8.2_

  - [x] 8.2 Build the `Hero` with the animated demo preview
    - Create `apps/web/src/components/landing/hero.tsx`: H1 (48–60px, `tracking-tight`, 600) communicating "See how real ATS systems evaluate your resume", fully visible without scroll at ≥768px tall; subheadline ≤150 chars at `text-muted`; primary CTA "Get started — it's free" (Signature_Gradient on hover, ≥44px) → `/register`; a self-contained animated demo gauge counting 0→sample over 1200ms with **illustrative placeholder data** plus the honesty note "Basic keyword match — semantic analysis coming soon"; background pattern ≤10% opacity preserving AA. Staggered fade-up 600ms total / 100ms between; all final-state instantly under reduced motion.
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 5.1, 5.4; design Section 8.2_

  - [x] 8.3 Build `FeatureCard`
    - Create `apps/web/src/components/landing/feature-card.tsx`: Lucide icon, title ≤40 chars, description ≤120 chars; hover = shadow elevation + border highlight (200ms); optional `badge: "Coming soon" | "Planned"` distinguishing a Roadmap_Feature with **no enabled control** implying availability. Responsive grid (1col <640px, 2col 640–1024px, 4col >1024px) applied by the parent section.
    - _Requirements: 4.1, 4.6, 5.2, 5.3, 5.5, 16.10; design Section 7.3 (FeatureCard)_

  - [x] 8.4 Build the `HowItWorks` section
    - Create `apps/web/src/components/landing/how-it-works.tsx`: numbered three-step flow (upload resume → paste JD → get ATS score) with visual connectors; horizontal >768px, vertical ≤768px; scroll fade-up at 20% of the viewport (400ms), final-state under reduced motion.
    - _Requirements: 4.2, 4.8, 4.9; design Section 8.2_

  - [x] 8.5 Build `TrustSignals` and the `About` anchor
    - Create `apps/web/src/components/landing/trust-signals.tsx`: only truthful claims (privacy-first, PDF & DOCX, secure handling, fast ATS), each with an icon + short description; **no fabricated metrics/testimonials/logos**. Include the `#about` section with a truthful Phase-1 capability description (ATS simulation + resume-match analysis) as the "About" nav target.
    - _Requirements: 4.3, 4.4, 4.7, 5.1; design Section 8.2_

  - [x] 8.6 Build the `FinalCTA`
    - Create `apps/web/src/components/landing/final-cta.tsx`: sign-up button → `/register`, ≥44×44px, Signature_Gradient on hover, plus a single supporting line ≤80 chars; scroll fade-up at 20% of the viewport.
    - _Requirements: 4.5, 4.8, 4.9; design Section 8.2_

  - [x] 8.7 Assemble the marketing page, SEO metadata, and sitemap entry (remove legacy page)
    - Create `apps/web/src/app/(marketing)/page.tsx` composing GlassNav → Hero → Features grid → HowItWorks → TrustSignals/About → FinalCTA → `<footer>`, in logical order with `header/nav/main/footer` landmarks and exactly one `<h1>`. Export page metadata via the Metadata API (title ≤60, description ≤155, self-referential canonical, Open Graph, Twitter `summary_large_image`) using `lib/seo` helpers; use `next/image` with explicit width/height. **Remove the legacy `apps/web/src/app/page.tsx` (and `hero-text.tsx` once superseded) in the same task** to avoid a `/` route conflict; confirm `/` is the entry in `app/sitemap.ts`.
    - _Requirements: 4.8, 7.1, 7.2, 7.4, 7.5, 7.6, 21.7, 21.9; design Section 6.2, Section 8.2_

  - [x] 8.8 Unit tests for Landing components
    - GlassNav has no "Pricing"/non-MVP link and toggles to glass past the hero; Hero honesty note present and scoring never described as semantic/AI/LLM; FeatureCard `badge` renders no usable control; feature-grid column counts; reduced-motion renders the final state.
    - _Requirements: 3.8, 4.1, 5.1, 5.2, 5.5, 6.2; design Testing Strategy_

- [x] 9. Cross-cutting validation (Playwright gates + accessibility, all four screens, both themes)
  - [x] 9.1 Playwright visual/layout acceptance gates — ATS Results flagship (highest priority)
    - Author `apps/web/tests/visual/results.spec.ts` rendering fixtures A and C in both themes (via a test route or MSW-mocked `GET /api/v1/matches/[id]`) and assert (design Section 9.3): no horizontal scroll at 1280/1440/1920/390; gauge + qualitative label `bottom ≤ 720` @1280×720; gauge + both breakdown bars + the "Matched keywords" heading `bottom ≤ 900` @1440×900; full content within two viewport heights @1280×720; mobile gauge ≥120px and score ≥24px @390; body background matches the resolved `--color-bg` per theme; no placeholder/`lorem`/`ipsum`/`undefined`/`NaN`/`[object Object]`/raw field names (`scorer_version` only inside the styled footnote); `job_description_text` absent from DOM **and** network payload; degenerate state (fixture C) present and **not** styled with `danger`. Commit baseline screenshots per (viewport × theme).
    - _Requirements: 11.8, 12.5, 12.7, 14.1, 14.2, 14.3, 14.4, 14.5, 14.6, 14.7, 18.1, 18.5, 20.5; design Section 9.3, 9.4_

  - [x] 9.2 Playwright visual/layout gates — Upload, Auth, Landing
    - Author `apps/web/tests/visual/{upload,auth,landing}.spec.ts`: assert no horizontal scrollbar across 320–1920px (and at 390px), correct dark **and** light rendering with a committed screenshot per (screen × viewport × theme), and touch targets ≥44px on primary actions. Landing additionally asserts exactly one `<h1>` and the `header/nav/main/footer` landmarks render.
    - _Requirements: 7.2, 8.6, 9.13, 14.4, 18.1, 18.2; design Section 9.2, 9.4_

  - [x] 9.3 axe-core + computed-contrast accessibility verification (both themes, all screens)
    - Run axe-core against each rendered screen (Results, Upload, Auth, Landing) in **both** Dark_Mode and Light_Mode; assert one `<h1>`, sequential heading levels, landmarks, 2px branded focus ring visible, keyboard tab order following reading order, icon-only buttons carry `aria-label`, and form errors announce via `aria-live` within 1s. Add the computed-contrast assertions for the Section 10.1 token pairs, and explicitly verify the **mandated light-mode pill mitigation** (KeywordTag uses tinted-fill + `border` + primary `text`, not colored text). **Checklist note (manual, required for full WCAG validation):** screen-reader pass (VoiceOver + NVDA), keyboard-only completion of Landing→Register→Upload→Results, OS reduced-motion, and zoom-to-200% reflow — documented in the PR before the gate closes.
    - _Requirements: 14.6, 16.10, 16.11, 17.8, 19.1, 19.2, 19.3, 19.4, 19.5, 19.6, 19.7; design Section 10_

  - [x] 9.4 Non-indexing, sitemap, and SEO assertions across routes
    - Extend `apps/web/tests/non-indexing.test.ts`: `(app)` (`/upload`, `/matches/[id]`) and `(auth)` (`/login`, `/register`) export/inherit `robots: { index: false, follow: false }` and are excluded from `app/sitemap.ts`; `/` is indexable and present in the sitemap; the `proxy.ts` security headers / `X-Robots-Tag` behavior is unchanged (preserve `apps/web/tests/proxy.test.ts`).
    - _Requirements: 7.5, 8.7, 8.8, 8.9, 8.10, 13.3, 21.10; seo.md, security.md_

- [x] 10. Final checkpoint
  - Ensure the full suite passes — Vitest unit + integration, axe-core accessibility in both themes, and all Playwright visual/layout gates across the four screens — and that no screen leaks `job_description_text` or introduces non-contract fields. Confirm the flagship ATS Results page meets every Requirement 14 criterion in both themes (Req 14.8). Ensure all tests pass, ask the user if questions arise.

## Notes

- **All tasks are mandatory.** The test sub-tasks (previously marked optional with `*`) are now required: every phase's Vitest unit/integration tests, the Playwright visual/layout gates, and the axe-core + computed-contrast accessibility checks must be completed alongside the implementation tasks.
- **No property-based-testing tasks exist by design.** The design's Testing Strategy explicitly establishes that PBT does not apply to this UI-rendering/layout feature (no Correctness Properties section); correctness is verified by Vitest example/edge unit tests (co-located with components that have logic), axe-core + computed-contrast accessibility checks, integration tests over the Section 5 fixtures, and Playwright visual/layout acceptance gates.
- **Build order is flagship-first (Req 14.8):** foundation → shared primitives → **ATS Results** → Upload → Auth → Landing → consolidated validation. Upload precedes Auth/Landing because Results + Upload form the core authenticated flow.
- **Validation is consolidated near the end** (phase 9) per the requested structure: Playwright screenshot/layout gates and axe-core + computed-contrast accessibility in both themes. The flagship's gates (9.1) are listed first there and are the highest-priority checks, satisfying the flagship-first acceptance focus.
- **Backend contract (Req 20) is fixed:** all data types are imported from `packages/shared-types`; no task introduces suggestion `title`/`priority`, a third score dimension, or `job_description_text`.
- **Routes and the security-headers proxy are preserved (Req 21.10):** the `(marketing)` group + `sitemap.ts` (public only) are added; `(app)`/`(auth)` stay noindex; the legacy `dashboard`/`library`/`forgot-password`/`reset-password` folders are out of scope.
- **Design-review gate (Req 22) is satisfied:** the approved design IS the gate artifact, so no task re-creates wireframes or mockups; the Section 8 specs are implemented directly and verified by the Section 9/10 gates.
- Each task references the requirement clauses and/or design sections it satisfies; tasks build incrementally and end with wiring/validation, leaving no orphaned code.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3", "1.4"] },
    { "id": 1, "tasks": ["1.5", "1.6"] },
    { "id": 2, "tasks": ["1.7", "2.1", "2.2", "2.3", "2.4", "2.5", "2.6"] },
    { "id": 3, "tasks": ["2.7", "3.1", "3.2", "3.3", "3.4", "3.5"] },
    { "id": 4, "tasks": ["3.6", "4.1"] },
    { "id": 5, "tasks": ["4.2", "6.1", "6.2"] },
    { "id": 6, "tasks": ["6.3", "7.1", "8.1", "8.3"] },
    { "id": 7, "tasks": ["6.4", "7.2", "8.2", "8.4", "8.5", "8.6"] },
    { "id": 8, "tasks": ["6.5", "7.3", "8.7"] },
    { "id": 9, "tasks": ["7.4", "8.8", "9.1", "9.2", "9.3", "9.4"] }
  ]
}
```
