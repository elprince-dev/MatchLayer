# Requirements Document

## Introduction

This document specifies the requirements for a complete UI/UX redesign of the MatchLayer platform, transforming it from a developer-project aesthetic into a premium, venture-funded AI SaaS product comparable to Linear, Ashby, and Mercury. The redesign prioritizes design depth over feature breadth, focusing exclusively on the four primary Phase 1 MVP experiences: Landing Page, Authentication, Resume Upload, and ATS Results.

The ATS Results page is treated as the flagship experience — a recruiter, hiring manager, or engineering interviewer viewing a screenshot must immediately believe this is a production SaaS product.

Design principles: dark mode first, information hierarchy over decoration, whitespace as a feature, purposeful animations only, and a product that feels expensive. WCAG AA minimum compliance throughout.

**MVP scope constraint:** The application contains exactly four user-facing flows: Landing Page → Login/Register → Upload Resume + Job Description → ATS Results. No dashboard, analytics, settings, pricing, notifications, or admin functionality exists. The redesign must not reference or depend on non-existent functionality.

**Backend alignment constraint:** All UI requirements are bound to the existing FastAPI backend contract (`apps/api/src/matchlayer_api/api/matches/schemas.py` and `.../resumes/schemas.py`). The redesign must not require response fields that the backend does not already return. Requirement 20 codifies this and includes the authoritative data contract.

## Glossary

- **Design_System**: The unified set of visual tokens (colors, typography, spacing, radius, shadows, motion) and reusable components governing the appearance and behavior of all MatchLayer frontend surfaces.
- **Landing_Page**: The public marketing homepage at `/` that introduces MatchLayer to new visitors and drives sign-up conversions. Route classification: Public.
- **ATS_Results_Page**: The authenticated screen at `/matches/[id]` displaying the full ATS match analysis (overall score, score breakdown, matched/missing keywords, suggestions) for a single persisted Match_Result. Route classification: Authenticated.
- **Upload_Page**: The authenticated screen at `/upload` where users upload a resume file and paste a job description to initiate an ATS match. Route classification: Authenticated.
- **Auth_Pages**: The login and registration screens at `/login` and `/register`. Route classification: Authenticated for indexing purposes (noindex, nofollow, excluded from sitemap), but publicly reachable without a session.
- **Match_Result**: The persisted result of one scoring operation, addressed by a UUIDv7 id and viewed at `/matches/[id]`. Its wire shape is the backend `MatchResponse` (see Requirement 20).
- **Component_Library**: The collection of reusable UI components (ATS gauge, Score breakdown card, Upload widget, Keyword tags, Suggestion card, Skeleton loader, Error state) built on shadcn/ui patterns and copied into the repository.
- **Motion_System**: The standardized set of Framer Motion animations governing page transitions, hover states, card interactions, loading feedback, and the score reveal.
- **Score_Reveal**: The animated presentation of the ATS match score on the Results page, using an animated count-up from zero to the final score with the signature gradient applied to the score number.
- **Glass_Nav**: A frosted, semi-transparent navigation bar using backdrop-blur, fixed at the top of the viewport on the Landing_Page.
- **Skeleton_Loader**: A placeholder UI element that mimics the shape and layout of content while data loads, using a shimmer animation to indicate activity.
- **Empty_Result_State**: A defined presentation shown on the ATS_Results_Page when a Match_Result contains little or no useful data (zero score, no matched keywords, no missing keywords, or only the affirmative suggestion).
- **Error_State**: A screen or inline message shown when an operation fails, providing clear explanation and actionable recovery steps without exposing technical details.
- **Dark_Mode**: The default color theme using dark backgrounds (`#0A0A0B`) and light text, managed by next-themes. This is the application's initial default when no user preference is stored.
- **Light_Mode**: The alternate color theme using light backgrounds (`#FFFFFF`) and dark text, available via user toggle.
- **WCAG_AA**: Web Content Accessibility Guidelines level AA, requiring a minimum contrast ratio of 4.5:1 for normal text and 3:1 for large text.
- **Signature_Gradient**: A 135-degree linear gradient from brand violet to brand cyan, used sparingly as a punctuation mark for the score reveal, primary CTA hover, and brand mark.
- **CTA**: Call-to-action — a prominent interactive element directing users toward a primary conversion action.
- **Roadmap_Feature**: A capability that is planned but not implemented in Phase 1 (e.g., semantic/embeddings-based analysis). Must be visually distinguished from current functionality.
- **Design_Artifacts**: The set of information architecture, wireframes, component inventory, and high-fidelity mockups that must be produced and approved before implementation begins.

## Requirements

### Requirement 1: Design System Token Implementation

**User Story:** As a developer, I want a centralized design token system defined in CSS custom properties and consumed by Tailwind, so that visual consistency is enforced across all screens without inline styles or magic values.

#### Acceptance Criteria

1. THE Design_System SHALL define color tokens for background, elevated background, glass surface, border, strong border, primary text, muted text, subtle text, brand violet, brand cyan, success, warning, and danger — each with distinct light and dark mode values as specified in the design steering document.
2. THE Design_System SHALL define typography tokens using Geist Sans as the primary typeface and Geist Mono for code, scores, and identifiers, loaded via next/font with variable font optimization.
3. THE Design_System SHALL define a type scale hierarchy with H1 at 48-60px, H2 at 32-40px, H3 at 24px, and body at 16px, with letter-spacing set to Tailwind's `tracking-tight` class value on display text (H1, H2) and font-weight capped at semibold (600).
4. THE Design_System SHALL define spacing tokens on a 4px base grid aligned with Tailwind's default spacing scale, border-radius tokens (12px default for cards and buttons, 16px for hero surfaces, full-round for badges), and shadow tokens using layered multi-stop values (e.g., `0_1px_2px_rgba(0,0,0,0.04), 0_8px_24px_rgba(0,0,0,0.08)` for elevated surfaces) with no single-layer uniform-spread shadows.
5. THE Design_System SHALL define motion tokens with 200ms duration for micro-interactions, 400ms for layout transitions, and 600ms for hero reveals, using an ease-out exponential curve `[0.16, 1, 0.3, 1]`.
6. THE Design_System SHALL define the Signature_Gradient token as `linear-gradient(135deg, var(--brand) 0%, var(--brand-2) 100%)`.
7. WHEN a component references a visual property, THE Design_System SHALL provide that value through Tailwind theme configuration reading CSS custom properties defined in globals.css, not through inline hex values or arbitrary Tailwind bracket-notation classes (e.g., `text-[#333]`, `bg-[#fff]`).
8. WHEN the user's next-themes selection changes between Dark_Mode and Light_Mode, THE Design_System SHALL switch all color token values by applying the corresponding set of CSS custom properties scoped to the `dark` class in globals.css, with no component-level conditional logic required.
9. IF the user has enabled `prefers-reduced-motion: reduce`, THEN THE Design_System SHALL provide a motion duration token override of 0ms for all animation durations so that consuming components can respect the preference through the token value.

### Requirement 2: Theme Management (Dark and Light only)

**User Story:** As a user, I want the application to default to dark mode with the option to switch to light mode, so that I have a comfortable, consistent viewing experience that the product remembers.

#### Acceptance Criteria

1. THE Design_System SHALL support exactly two themes: Dark_Mode and Light_Mode. THE Design_System SHALL NOT expose a "System" theme option, and the theme control SHALL present only Dark_Mode and Light_Mode choices.
2. IF no user theme preference is stored, THEN THE Design_System SHALL render in Dark_Mode as the initial default, regardless of the operating system `prefers-color-scheme` value.
3. THE Design_System SHALL provide a theme toggle that switches between Dark_Mode and Light_Mode only.
4. WHEN the user selects a theme via the toggle, THE Design_System SHALL persist that preference so that on subsequent visits the selected theme is applied before any content is painted to the screen.
5. THE Design_System SHALL provide visually distinct and complete token sets for both Dark_Mode and Light_Mode covering surfaces, text, borders, and interactive elements, where all text and UI components meet WCAG AA contrast ratios (4.5:1 for normal text, 3:1 for large text and interactive components) in both themes.
6. WHEN the page loads, THE Design_System SHALL apply the resolved theme (stored preference if present, otherwise Dark_Mode) via a blocking script injected before first paint so that no frame of an incorrect theme is visible to the user.
7. THE next-themes configuration SHALL set `defaultTheme` to "dark" and SHALL set `enableSystem` to false, so the System option is disabled at the library level.
8. THE theme toggle SHALL communicate its current state to assistive technology via an accessible label that reflects the active theme.

### Requirement 3: Landing Page Hero Section

**User Story:** As a new visitor, I want to immediately understand what MatchLayer does and feel compelled to sign up, so that I convert from visitor to user within seconds of landing.

#### Acceptance Criteria

1. THE Landing_Page SHALL display a hero section with a headline that communicates "See how real ATS systems evaluate your resume" fully visible without vertical scrolling on viewports 768px tall or greater at page load.
2. THE Landing_Page hero section SHALL display a subheadline of no more than 150 characters providing supporting context about ATS simulation, rendered at the text-muted color token.
3. THE Landing_Page hero section SHALL display a primary CTA button with the label "Get started — it's free" using the brand color as background with the Signature_Gradient applied on hover, navigating to the `/register` page.
4. THE Landing_Page hero section SHALL display a self-contained animated ATS demo preview containing a circular gauge that animates a count-up from 0 to a fixed sample score value over a duration of 1200ms, using clearly illustrative placeholder data not connected to any real analysis, to demonstrate the product's visual output before sign-up.
5. THE Landing_Page hero section SHALL use a background element (dot grid, animated grid, or noise pattern) rendered at no more than 10% opacity so that all foreground text maintains WCAG AA contrast against the page background.
6. THE Landing_Page hero section SHALL reveal content elements (headline, subheadline, CTA, demo preview) with a staggered fade-up animation using the Motion_System 600ms total duration and a 100ms delay between each successive element.
7. THE Landing_Page hero section SHALL render the headline at the H1 type scale (48–60px) with tracking-tight letter-spacing and font-semibold (600) weight.
8. IF the user has enabled prefers-reduced-motion, THEN THE Landing_Page hero section SHALL disable all animations and display content in its final state immediately without fade-up, stagger, background animation, or gauge count-up.

### Requirement 4: Landing Page Features and Trust Signals

**User Story:** As a visitor scrolling past the hero, I want to see what the product offers and why I can trust it with my resume, so that I build confidence before signing up — without being shown fabricated metrics.

#### Acceptance Criteria

1. THE Landing_Page SHALL display a features section with at least four feature cards, each containing a Lucide icon, a title of no more than 40 characters, and a description of no more than 120 characters, arranged in a grid that displays 1 column on viewports below 640px, 2 columns between 640px and 1024px, and 4 columns above 1024px.
2. THE Landing_Page SHALL display a "How it works" section with a numbered three-step flow (upload resume, paste job description, get ATS score) using visual connectors (lines or arrows) between steps, laid out horizontally on viewports above 768px and vertically on viewports at or below 768px.
3. THE Landing_Page SHALL display a trust-signals section communicating only truthful, supportable claims about the product. The section SHALL NOT display fabricated metrics such as user counts, resumes processed, score-accuracy percentages, customer testimonials, or company logos.
4. THE Landing_Page trust-signals section SHALL be drawn from the following supportable claims: privacy-first processing, PDF and DOCX support, secure file handling, and fast ATS analysis. Each claim SHALL be presented with an icon and a short supporting description, and each claim SHALL describe a capability that exists in the current MVP.
5. THE Landing_Page SHALL display a final CTA section with a sign-up button that navigates to the `/register` page, styled at minimum 44×44px touch target size with the Signature_Gradient applied on hover, accompanied by a single line of supporting text of no more than 80 characters.
6. THE Landing_Page feature cards SHALL use hover effects consisting of an elevation change (shadow increase) and a border highlight, animated following the Motion_System 200ms micro-interaction timing and ease-out exponential curve.
7. THE Landing_Page SHALL display an "About" section containing a concise, truthful description of MatchLayer's purpose (ATS simulation and resume-match analysis) and its current Phase 1 capabilities, serving as the anchor target for the "About" navigation link.
8. THE Landing_Page sections (features, how it works, trust signals, about, CTA) SHALL appear in a logical order below the hero and SHALL use scroll-driven fade-up reveal animations triggered when the section enters 20% of the viewport, following the Motion_System 400ms layout transition timing.
9. WHEN the user has enabled prefers-reduced-motion in their operating system, THE Landing_Page SHALL display all section content in its final visible state immediately without fade-up or hover animations.

### Requirement 5: Roadmap vs Current Functionality Honesty

**User Story:** As a visitor, I want the landing page to clearly distinguish what the product does today from what is planned, so that I am never misled into believing a planned capability already exists.

#### Acceptance Criteria

1. THE Landing_Page SHALL describe the current scoring capability accurately as keyword and TF-IDF based matching, and SHALL NOT describe the current scoring as semantic, embeddings-based, AI-powered, or LLM-powered.
2. IF the Landing_Page references a Roadmap_Feature (such as semantic analysis), THEN it SHALL visually distinguish that feature from current capabilities using an explicit label such as "Coming soon" or "Planned" adjacent to the feature.
3. THE Landing_Page SHALL NOT place Roadmap_Features in the primary features grid alongside current capabilities without the distinguishing label required by criterion 2.
4. THE Landing_Page SHALL present the honesty note "Basic keyword match — semantic analysis coming soon" where the scoring approach is described, so the product does not overstate the current capability.
5. WHERE a Roadmap_Feature is shown, THE Landing_Page SHALL NOT provide a control implying the feature is usable now (no enabled button, link, or toggle that suggests the planned capability is available).

### Requirement 6: Landing Page Navigation

**User Story:** As a visitor, I want a premium navigation bar that stays accessible as I scroll, so that I can learn more or sign up at any point.

#### Acceptance Criteria

1. THE Landing_Page SHALL display a Glass_Nav fixed at the top of the viewport with a backdrop-blur of 12px and the `bg-glass` token background (65% opacity in light mode, 55% opacity in dark mode).
2. THE Glass_Nav SHALL display the MatchLayer brand mark, in-page navigation links ("Features", "How It Works", "About"), a "Sign in" ghost button, a "Get started" primary button, and a theme toggle. THE Glass_Nav SHALL NOT display a "Pricing" link or any link to functionality that does not exist in the MVP.
3. WHEN a navigation link ("Features", "How It Works", "About") is activated, THE Glass_Nav SHALL scroll the page to the corresponding section anchor on the Landing_Page.
4. WHILE the viewport width is below 768px, THE Glass_Nav SHALL collapse navigation links into a mobile menu accessible via a hamburger button that communicates expanded or collapsed state to assistive technology.
5. WHEN the user scrolls past the bottom edge of the hero section, THE Glass_Nav SHALL transition from a fully transparent background to the glass surface appearance within 200ms.
6. IF the user scrolls back above the bottom edge of the hero section, THEN THE Glass_Nav SHALL transition back to the fully transparent background within 200ms.

### Requirement 7: Landing Page SEO and Performance

**User Story:** As a product owner, I want the landing page to be fully indexable and performant, so that it ranks well in search engines and loads quickly.

#### Acceptance Criteria

1. THE Landing_Page SHALL export SEO metadata via the Next.js Metadata API including: a unique title (≤ 60 characters), a meta description (≤ 155 characters), a self-referential canonical URL, Open Graph tags (og:title, og:description, og:image, og:url, og:type), and Twitter Card tags (card type summary_large_image).
2. THE Landing_Page SHALL use semantic HTML with exactly one h1 element, no skipped heading levels (e.g., h1 followed by h3 without an intervening h2), and landmark elements (header, nav, main, footer).
3. THE Landing_Page SHALL achieve Core Web Vitals targets when measured via Lighthouse in mobile simulation mode (simulated throttling, default Lighthouse settings): LCP under 2.5 seconds, CLS under 0.1, and INP under 200 ms.
4. THE Landing_Page SHALL use next/image for all raster images with explicit width and height attributes to prevent layout shift.
5. THE Landing_Page (`/`) SHALL be included in the sitemap generated by app/sitemap.ts and SHALL be indexable (no noindex directive).
6. THE Landing_Page SHALL provide descriptive alt text on all informational images (non-empty, ≤ 125 characters) and empty alt attributes (alt="") for purely decorative images.

### Requirement 8: Authentication Pages

**User Story:** As a user, I want professional, trustworthy login and registration pages that feel premium and secure, so that I feel confident providing my credentials.

#### Acceptance Criteria

1. THE Auth_Pages SHALL display a centered card layout (max-width 448px) with the MatchLayer brand mark at the top, a submit button, and the following form fields: email and password for the login view; email, password, and confirm-password for the registration view.
2. THE Auth_Pages SHALL display trust signals including links to the privacy policy and terms of service below the form.
3. THE Auth_Pages SHALL provide a link to switch between login and registration views.
4. WHEN a form validation error occurs, THE Auth_Pages SHALL display the error message inline adjacent to the relevant field with an aria-live="polite" announcement, validating at minimum: email format, password minimum length of 12 characters, and confirm-password match on the registration view.
5. THE Auth_Pages SHALL use a subtle animated background (gradient mesh or noise pattern) that does not overlay or obscure the form card, and SHALL disable the animation when the user has enabled prefers-reduced-motion.
6. THE Auth_Pages SHALL be responsive, centering the card vertically and horizontally on viewport widths from 320px to at least 1920px.
7. THE `/login` page SHALL export robots metadata `index: false, follow: false` (noindex, nofollow) and SHALL set the `X-Robots-Tag: noindex, nofollow` response header.
8. THE `/register` page SHALL export robots metadata `index: false, follow: false` (noindex, nofollow) and SHALL set the `X-Robots-Tag: noindex, nofollow` response header.
9. THE `/login` and `/register` pages SHALL be excluded from the sitemap generated by app/sitemap.ts.
10. THE redesign SHALL NOT add the authentication routes to robots.txt allow rules in a way that invites crawling; the non-indexing controls in criteria 7–9 take precedence and the landing page (`/`) remains the only indexable surface among these screens.
11. IF a login or registration submission fails due to invalid credentials or a server error, THEN THE Auth_Pages SHALL display a non-enumerable error message above the form (identical wording for "user not found" and "wrong password") and SHALL preserve the user's entered email value.
12. WHEN a user successfully authenticates, THE Auth_Pages SHALL navigate the user to the Upload_Page as the post-authentication landing destination.

### Requirement 9: Upload Page Experience

**User Story:** As a user, I want a premium drag-and-drop upload experience with real-time feedback, so that I can submit my resume and job description confidently and enjoyably.

#### Acceptance Criteria

1. THE Upload_Page SHALL provide a drag-and-drop zone that accepts a single file of type PDF or DOCX up to 5MB, with a click-to-browse fallback.
2. WHEN a file is dragged over the drop zone, THE Upload_Page SHALL visually highlight the zone with a brand-colored border, background tint change, and updated instructional text.
3. WHEN a file is dropped or selected, THE Upload_Page SHALL display a file preview card showing the filename, file size in human-readable format (KB or MB), file-type icon, and a remove button.
4. WHEN an invalid file type or a file exceeding 5MB is provided, THE Upload_Page SHALL display an inline Error_State specifying the constraint that was violated (accepted formats: PDF, DOCX; maximum size: 5MB) and SHALL NOT display a file preview card.
5. WHILE file transmission is in progress, THE Upload_Page SHALL display an animated progress bar with a numeric percentage (0–100%) indicating upload completion.
6. WHEN the upload completes, THE Upload_Page SHALL reflect the returned resume `extraction_status`: WHILE the status is "pending" it SHALL indicate processing; IF the status is "failed" THEN it SHALL display an inline Error_State explaining the resume text could not be read and prompting the user to try a different file; and only WHEN the status is "succeeded" SHALL the resume be treated as ready for analysis.
7. IF file transmission itself fails (network or server error), THEN THE Upload_Page SHALL display an inline Error_State indicating the upload failure and SHALL allow the user to retry or remove the file.
8. THE Upload_Page SHALL provide a job description textarea input with placeholder text, a live character count displaying current and maximum characters, and guidance copy. The textarea SHALL enforce the backend bounds of a minimum of 30 characters and a maximum of 50,000 characters (measured on the trimmed value), matching `MATCHLAYER_JD_MIN_CHARS`/`MATCHLAYER_JD_MAX_CHARS`.
9. WHEN a resume with `extraction_status` "succeeded" is present and the trimmed job description length is between 30 and 50,000 characters, THE Upload_Page SHALL enable the "Analyze Match" submit button; otherwise the button SHALL remain disabled.
10. WHEN the user removes the uploaded file via the remove button, THE Upload_Page SHALL clear the file preview card and disable the "Analyze Match" submit button.
11. WHEN the submission is in progress, THE Upload_Page SHALL disable the submit button and display a loading state with contextual messaging ("Analyzing your resume...").
12. WHEN the analysis (`POST /api/v1/matches`) returns a created Match_Result, THE Upload_Page SHALL navigate the user to `/matches/[id]` using the returned `id`.
13. THE Upload_Page SHALL export robots metadata as noindex, nofollow and set the `X-Robots-Tag: noindex, nofollow` response header per the Authenticated route classification.

### Requirement 10: ATS Results Page — Score Visualization

**User Story:** As a user, I want to see my ATS score presented with a dramatic, premium reveal animation and clear circular gauge, so that the moment feels significant and the product feels impressive.

#### Acceptance Criteria

1. THE ATS_Results_Page SHALL display the Match_Result `score` (an integer 0-100) using a circular gauge visualization with an animated stroke that fills clockwise to the score percentage, where 0 shows no fill and 100 shows a complete circle.
2. THE ATS_Results_Page SHALL apply the Score_Reveal animation: an animated count-up from zero to the final `score` over 600ms with the ease-out exponential easing curve (`[0.16, 1, 0.3, 1]`).
3. THE ATS_Results_Page SHALL render the score number in Geist Mono at `text-6xl` size with the Signature_Gradient (violet-to-cyan linear gradient at 135 degrees) applied as a text gradient.
4. THE ATS_Results_Page SHALL display a qualitative label below the score that maps to the following ranges: "Excellent" for scores 80-100, "Good" for scores 60-79, "Fair" for scores 40-59, and "Needs Work" for scores 0-39.
5. WHEN the score data is loading, THE ATS_Results_Page SHALL display a Skeleton_Loader matching the circular gauge dimensions and layout.
6. WHILE the user has `prefers-reduced-motion` enabled, THE ATS_Results_Page SHALL skip the count-up animation and the gauge stroke animation, displaying the final score and filled gauge immediately without motion.
7. THE ATS_Results_Page SHALL apply the Signature_Gradient as the gauge stroke color and render the gauge background using the `bg-elevated` design token.

### Requirement 11: ATS Results Page — Breakdown, Keywords, and Suggestions

**User Story:** As a user, I want to see how my score was composed and which keywords matched or are missing, so that I understand exactly where I stand and what to improve — using only the data the backend actually returns.

#### Acceptance Criteria

1. THE ATS_Results_Page SHALL display the two score-breakdown components returned in `score_breakdown`: the `similarity_component` (TF-IDF cosine similarity) and the `keyword_coverage_component` (fraction of analyzed keywords present in the resume), each rendered as a labeled progress bar scaled from its `[0,1]` value to a 0–100% display. THE page SHALL NOT display any third scoring dimension (such as "experience relevance") that the backend does not return.
2. THE ATS_Results_Page SHALL present the component weights (`weight_similarity`, `weight_keyword`) alongside the breakdown so the composition of the final score is explainable.
3. THE ATS_Results_Page SHALL display a matched-keywords section listing the `matched_keywords` (each a `{term, weight}` object) as success-colored pill tags, ordered by descending weight as received from the API.
4. THE ATS_Results_Page SHALL display a missing-keywords section listing the `missing_keywords` (each a `{term, weight}` object) as warning-colored pill tags, ordered by descending weight as received from the API.
5. THE ATS_Results_Page SHALL display a suggestions section rendering the `suggestions` array (each a `{keyword, text}` object) as cards, where each card shows the suggestion `text` and, when `keyword` is non-empty, associates the card with that keyword. THE page SHALL NOT render a suggestion title or a priority indicator, because the backend `SuggestionOut` contract provides neither.
6. THE ATS_Results_Page keyword tags SHALL use uniform height, consistent horizontal and vertical spacing, and be grouped into their respective matched and missing sections to allow visual distinction between the two groups.
7. THE ATS_Results_Page breakdown and suggestion cards SHALL use staggered fade-up entrance animations with 400ms duration per card and 100ms delay between each successive card, following the Motion_System layout transition timing, and SHALL render in their final state immediately when prefers-reduced-motion is enabled.
8. THE ATS_Results_Page SHALL display the `scorer_version` somewhere on the page (e.g., a footnote) so the result is attributable to a scoring version, and SHALL NOT display `job_description_text` (the backend never returns it).

### Requirement 12: ATS Results Page — Empty and Degenerate Result States

**User Story:** As a user whose analysis produced little or no useful data, I want clear, encouraging messaging instead of blank or broken sections, so that I understand the outcome and know what to do next.

#### Acceptance Criteria

1. IF `score` is 0, THEN THE ATS_Results_Page SHALL still render the gauge at 0% with the "Needs Work" label and SHALL display an explanatory message that little or no overlap was found between the resume and the job description, rather than an empty or error screen.
2. IF the `matched_keywords` array is empty, THEN THE ATS_Results_Page SHALL display a defined message in the matched-keywords section indicating no matching keywords were found, instead of an empty section.
3. IF the `missing_keywords` array is empty, THEN THE ATS_Results_Page SHALL display a defined message in the missing-keywords section indicating the resume covers the analyzed keywords from the job description, instead of an empty section.
4. WHEN the backend returns the single affirmative suggestion (a `suggestions` array of length 1 whose `keyword` is empty), THE ATS_Results_Page SHALL render that suggestion's `text` as a positive confirmation styled distinctly from improvement suggestions, and SHALL NOT label it with a missing keyword.
5. IF both `similarity_component` and `keyword_coverage_component` are 0 (the degenerate case produced when a resume or job description had insufficient extractable content), THEN THE ATS_Results_Page SHALL display an Empty_Result_State explaining that not enough readable content was available to produce a meaningful match and offering an "Analyze another job" action.
6. THE ATS_Results_Page SHALL treat a successfully returned Match_Result with low or zero values as a valid result (rendered per criteria 1–5), and SHALL reserve the Error_State (Requirement 13) for cases where the Match_Result could not be fetched at all.
7. THE Empty_Result_State and the per-section empty messages SHALL use neutral and success/warning tokens appropriately and SHALL NOT use the danger token, so a sparse-but-valid result does not read as an error.

### Requirement 13: ATS Results Page — Actions, Navigation, and Fetch Errors

**User Story:** As a user who has viewed my results, I want clear next actions and graceful handling when results cannot load, so that I never hit a dead end.

#### Acceptance Criteria

1. THE ATS_Results_Page SHALL provide an "Analyze another job" primary CTA button that navigates the user to the Upload_Page when activated.
2. THE ATS_Results_Page SHALL NOT display navigation to non-existent functionality such as a dashboard, analytics, or a saved-match history list. (The backend exposes `GET /api/v1/matches`, but no history UI exists in the MVP and none SHALL be implied.)
3. THE ATS_Results_Page SHALL export robots metadata as `noindex, nofollow` and set the `X-Robots-Tag: noindex, nofollow` response header per the Authenticated route classification.
4. WHILE the Match_Result is being fetched, THE ATS_Results_Page SHALL display a Skeleton_Loader that matches the layout shape of the results content.
5. IF the `GET /api/v1/matches/{id}` request returns a network error, a server error (5xx), or does not respond within 10 seconds, THEN THE ATS_Results_Page SHALL display an Error_State containing a plain-language message that the result could not be loaded, a retry button that re-attempts the fetch, and a link to the Upload_Page.
6. IF the requested match returns 404 not_found (the match does not exist, was deleted, or belongs to another user), THEN THE ATS_Results_Page SHALL display an Error_State that does not reveal whether the match exists for another user, offering a link to the Upload_Page.

### Requirement 14: ATS Results Page — Flagship Showcase (Measurable)

**User Story:** As the product owner building a portfolio-quality product, I want the ATS Results page to be the most polished screen, with objectively verifiable layout guarantees, so that a screenshot can be used directly in showcase contexts.

#### Acceptance Criteria

1. WHEN rendered at a 1280×720 desktop viewport with a representative Match_Result, THE ATS_Results_Page SHALL display the circular score gauge and the score's qualitative label fully within the first 720px of vertical height (above the fold) without scrolling.
2. WHEN rendered at a 1440×900 desktop viewport with a representative Match_Result, THE ATS_Results_Page SHALL present the score gauge, the two breakdown component bars, and at least the matched-keywords section heading within a single viewport height (≤ 900px) without horizontal scrolling.
3. THE ATS_Results_Page SHALL contain no placeholder text, lorem ipsum, raw field names, console/debug output, or unstyled (default-browser-styled) elements in its rendered output for a successful Match_Result.
4. THE ATS_Results_Page SHALL render with no horizontal scrollbar at viewport widths of 1280px, 1440px, and 1920px.
5. THE ATS_Results_Page SHALL render its complete content (score, breakdown, matched keywords, missing keywords, suggestions) within at most two viewport heights of vertical scrolling at a 1280×720 viewport, so the full result is capturable in at most two stacked screenshots.
6. THE ATS_Results_Page SHALL meet all Requirement 17 (Accessibility) and Requirement 1/2 token criteria in both Dark_Mode and Light_Mode, so screenshots in either theme are presentation-ready.
7. THE ATS_Results_Page SHALL use realistic, representative values supplied by an actual or fixture-backed Match_Result for any demonstration capture, with no fabricated fields beyond the backend contract.
8. THE ATS_Results_Page SHALL be the only screen designated highest visual-design priority; WHERE design or implementation effort must be prioritized, the ATS_Results_Page SHALL be completed to the criteria above before non-flagship polish on other screens is considered done.

### Requirement 15: Motion and Animation System

**User Story:** As a user, I want subtle, purposeful animations that reinforce hierarchy and provide feedback, so that the interface feels responsive and premium without being distracting.

#### Acceptance Criteria

1. THE Motion_System SHALL use Framer Motion as the sole animation library for all animations including page transitions, hover states, card interactions, upload feedback, and the Score_Reveal.
2. THE Motion_System SHALL enforce maximum animation durations of 200ms for micro-interactions (hover states, button presses, toggles), 400ms for layout transitions (page transitions, card reordering, list stagger sequences), and 600ms for hero reveals (Score_Reveal, initial page hero entrance), where the total elapsed time of a stagger sequence SHALL NOT exceed the category maximum for that animation type.
3. THE Motion_System SHALL use the ease-out exponential curve `[0.16, 1, 0.3, 1]` as the default easing function.
4. IF the user has enabled prefers-reduced-motion in their operating system, THEN THE Motion_System SHALL disable all animations except loading/progress indicators and focus-ring transitions, rendering animated content in its final state with zero duration.
5. THE Motion_System SHALL NOT use bounce effects except on success confirmations (match analysis complete).
6. THE Motion_System SHALL provide a shared motion-safe wrapper component or hook that conditionally applies animations based on the reduced-motion preference.
7. IF a navigation event or unmount occurs while an animation is in progress, THEN THE Motion_System SHALL cancel the running animation and display the target content in its final state within 50ms.

### Requirement 16: Reusable Component Library

**User Story:** As a developer, I want a set of reusable, well-typed UI components following shadcn/ui patterns and bound to the real API contract, so that I can build the MVP screens consistently without duplicating UI logic or inventing data fields.

#### Acceptance Criteria

1. THE Component_Library SHALL provide an ATS gauge component accepting a score (integer, 0–100) and rendering a circular progress visualization with an animated count-up (duration 600ms, easing ease-out) applying the Signature_Gradient.
2. THE Component_Library SHALL provide a Score breakdown component that accepts the two-component breakdown (`similarity_component`, `keyword_coverage_component`, `weight_similarity`, `weight_keyword`, `final_score`) and renders each component as a labeled progress bar, with no field that the backend `ScoreBreakdownOut` does not provide.
3. THE Component_Library SHALL provide an Upload widget component with a drag-and-drop zone accepting PDF and DOCX files up to 5 MB, a file preview card showing the original filename and file size, a determinate progress bar (0–100%), `extraction_status` reflection, and an inline error display.
4. THE Component_Library SHALL provide a Keyword tag component accepting a `{term, weight}` value and a variant ("success" for matched, "warning" for missing) rendered as a pill with `rounded-full` border radius.
5. THE Component_Library SHALL provide a Suggestion card component accepting a `{keyword, text}` value, rendering the `text` and optionally associating with `keyword` when non-empty. THE Suggestion card SHALL NOT require a title or priority prop, because the backend does not supply them.
6. THE Component_Library SHALL provide shared Skeleton_Loader and Error_State components reusable across the Upload_Page and ATS_Results_Page.
7. THE Component_Library SHALL provide only components consumed by the four MVP screens and SHALL NOT include components that serve non-existent functionality (e.g., dashboard KPI cards, analytics widgets, notification toasts beyond inline feedback).
8. THE Component_Library SHALL follow shadcn/ui patterns: components are copied into the repository, use Tailwind for styling via design tokens, accept a className prop for composition, and export TypeScript interfaces for all props, sourced from or compatible with the generated `packages/shared-types` definitions.
9. THE Component_Library SHALL NOT contain any inline styles, hardcoded hex values, or animation libraries other than Framer Motion.
10. THE Component_Library SHALL render all components correctly in both Light_Mode and Dark_Mode using CSS custom properties, with text meeting WCAG AA contrast ratio (4.5:1 normal, 3:1 large) in both modes.
11. THE Component_Library SHALL ensure all interactive elements within components are keyboard-accessible, include visible focus indicators, and respect the `prefers-reduced-motion` media query by disabling animations when the user preference is set to reduce.

### Requirement 17: Loading and Error States

**User Story:** As a user, I want consistent loading and error experiences across all screens, so that I always know what is happening and can recover from failures.

#### Acceptance Criteria

1. WHEN content is being fetched, THE Component_Library SHALL display Skeleton_Loader elements that match the dimensions and layout of the expected content with a shimmer animation cycling every 1.5 seconds.
2. THE Component_Library SHALL NOT use spinner-only loading indicators as the primary loading pattern on any screen.
3. WHEN an operation fails, THE Component_Library SHALL display an Error_State with a title of no more than 60 characters, a plain-language explanation of no more than 200 characters that avoids technical terminology, and at least one recovery action (retry button or navigation link).
4. THE Error_State SHALL NOT display technical error codes, stack traces, internal identifiers, or any RFC 7807 `type`/`request_id` field value to the user.
5. THE Error_State SHALL use the danger color token for the error indicator and the text or text-muted color tokens for explanatory text.
6. IF a network error occurs during data fetching, THEN THE Component_Library SHALL display an inline error with a retry button rather than navigating the user away from the current page.
7. IF a data fetch does not receive a response within 30 seconds, THEN THE Component_Library SHALL replace the Skeleton_Loader with an Error_State indicating a timeout and offering a retry action.
8. WHILE content is being fetched, THE Component_Library SHALL disable interactive elements within the loading region to prevent user actions on incomplete data.

### Requirement 18: Responsive Design

**User Story:** As a user on any device, I want the application to render correctly and remain usable from mobile to ultrawide displays, so that I can access MatchLayer from any screen.

#### Acceptance Criteria

1. THE Design_System SHALL support viewports from 320px (mobile) to 2560px (ultrawide) without horizontal scrolling or content overflow.
2. WHEN the viewport is below 768px, THE Design_System SHALL stack multi-column layouts into a single column, reduce section padding from py-24 to py-16, and ensure all interactive touch targets are at least 44×44px.
3. WHEN the viewport is above 1280px, THE Design_System SHALL constrain content width to max-w-7xl (1280px) for page layouts and max-w-3xl (768px) for prose content.
4. THE Design_System SHALL use responsive typography where body text renders at no smaller than 16px on mobile viewports and headings scale down by no more than one Tailwind text-size step from their desktop size, while maintaining the type hierarchy (h1 > h2 > h3 > body).
5. WHEN the viewport is below 768px, THE ATS_Results_Page circular gauge SHALL render at a minimum diameter of 120px and display the score number at no smaller than 24px.
6. WHILE the viewport is between 768px and 1279px, THE Design_System SHALL display layouts in a two-column grid where content permits, using the same spacing values as the desktop breakpoint.

### Requirement 19: Accessibility Compliance

**User Story:** As a user with disabilities, I want the application to meet WCAG AA standards, so that I can use all features with assistive technologies.

#### Acceptance Criteria

1. THE Design_System SHALL maintain a minimum contrast ratio of 4.5:1 for normal text (below 18pt regular or 14pt bold) and 3:1 for large text (18pt regular or 14pt bold and above) in both Dark_Mode and Light_Mode.
2. THE Design_System SHALL provide focus indicators with a minimum thickness of 2px and a contrast ratio of at least 3:1 against adjacent background colors, using the brand color, on all interactive elements reachable by keyboard navigation.
3. THE Component_Library SHALL ensure all interactive elements are operable via keyboard alone with tab order following the visual reading order (left-to-right, top-to-bottom within each landmark region).
4. WHEN a form error occurs, THE Component_Library SHALL announce the error via an aria-live region within 1 second of the error state being set, including identification of the invalid field and the reason for failure.
5. THE Design_System SHALL use semantic HTML with one h1 per page, sequential heading levels with no skipped levels (e.g., h2 follows h1, h3 follows h2), and landmark elements (header, nav, main, footer) on every page.
6. WHEN the user has enabled prefers-reduced-motion, THE Motion_System SHALL suppress all animations except loading indicators and progress bars, replacing motion-based transitions with instant state changes.
7. THE Component_Library SHALL provide alt text of no more than 125 characters that conveys the image's purpose for all informational images, empty alt attributes (alt="") for decorative images, and aria-labels that describe the action for all icon-only buttons.
8. THE Component_Library SHALL include a skip-navigation link as the first focusable element on each page that moves keyboard focus directly to the main content landmark.
9. IF a modal or dropdown is opened, THEN THE Component_Library SHALL trap keyboard focus within the open overlay until it is dismissed, and return focus to the triggering element upon close.

### Requirement 20: Backend Data Alignment

**User Story:** As a developer, I want every screen and mockup bound to the real backend response contract, so that the redesign never depends on fields that do not exist and no design rework is needed when wiring real data.

#### Acceptance Criteria

1. THE redesign Design_Artifacts and implemented screens SHALL be based only on the fields present in the existing FastAPI response models (`MatchResponse`, `ScoreBreakdownOut`, `KeywordOut`, `SuggestionOut`, `ResumeResponse`) as generated into `packages/shared-types`.
2. THE redesign mockups SHALL use realistic sample responses that conform to the data contract in criterion 6 (correct field names, types, and value ranges).
3. THE redesign SHALL NOT require, render, or assume any field absent from the backend response — explicitly including suggestion `title`, suggestion `priority`, a third "experience relevance" score dimension, or `job_description_text` on any match response.
4. IF a screen needs a field not present in the current backend contract, THEN that field SHALL be explicitly identified as a proposed backend change and approved before implementation; the redesign SHALL NOT silently introduce it on the frontend.
5. THE redesign SHALL treat `job_description_text` as Restricted PII that is never returned by the match API and therefore never displayed or relied upon by any screen.
6. THE expected ATS result data contract THE design SHALL conform to is the following (representative TypeScript, derived from the backend Pydantic models):

```typescript
// Source of truth: apps/api/src/matchlayer_api/api/matches/schemas.py
// Generated into packages/shared-types via openapi-typescript / openapi-zod-client.

interface Keyword {
  term: string; // normalized keyword term
  weight: number; // lexicon weight or TF-IDF score
}

interface Suggestion {
  keyword: string; // missing term addressed; "" only for the affirmative suggestion
  text: string; // plain-text, user-facing guidance (no title, no priority)
}

interface ScoreBreakdown {
  similarity_component: number; // TF-IDF cosine similarity, [0, 1]
  keyword_coverage_component: number; // fraction of analyzed keywords present, [0, 1]
  weight_similarity: number; // weight applied to similarity (default 0.6)
  weight_keyword: number; // weight applied to keyword coverage (default 0.4)
  final_score: number; // combined clamped integer, [0, 100]
}

interface MatchResponse {
  id: string; // UUIDv7
  resume_id: string; // UUIDv7
  score: number; // integer [0, 100]
  score_breakdown: ScoreBreakdown;
  matched_keywords: Keyword[]; // ordered by descending weight
  missing_keywords: Keyword[]; // ordered by descending weight
  suggestions: Suggestion[]; // ordered by descending missing-keyword weight
  scorer_version: string;
  created_at: string; // ISO 8601 UTC
  updated_at: string; // ISO 8601 UTC
  // NOTE: job_description_text is intentionally NOT returned (Restricted PII).
}

interface ResumeResponse {
  id: string; // UUIDv7
  original_filename: string;
  content_type: string; // magic-byte-detected
  byte_size: number;
  extraction_status: "pending" | "succeeded" | "failed";
  created_at: string; // ISO 8601 UTC
  updated_at: string; // ISO 8601 UTC
}
```

7. WHERE the generated `packages/shared-types` definitions and this representative contract diverge, THE generated types SHALL be authoritative, and the redesign SHALL be updated to match them rather than the reverse.

### Requirement 21: Technical Architecture Compliance

**User Story:** As a developer, I want the redesign to follow the established project architecture and conventions, so that the codebase remains maintainable and consistent.

#### Acceptance Criteria

1. THE Design_System SHALL be implemented using Next.js App Router with TypeScript strict mode enabled, including `noImplicitAny`, `strictNullChecks`, and `noUncheckedIndexedAccess` compiler options.
2. THE Design_System SHALL use Tailwind CSS for all styling, with design tokens defined in globals.css as CSS custom properties consumed via Tailwind theme configuration using @theme inline, and SHALL NOT use inline styles or hard-coded color values in components.
3. THE Component_Library SHALL use shadcn/ui as the base for primitive components (Button, Input, Dialog, Select).
4. THE Motion_System SHALL use Framer Motion as the sole animation library.
5. THE Design_System SHALL use next-themes for theme management, configured with `defaultTheme: "dark"` and `enableSystem: false` so Dark_Mode is the default and no System option exists (per Requirement 2).
6. THE Design_System SHALL use Lucide as the primary icon set, falling back to Phosphor or Tabler Icons only when the required icon is not available in Lucide.
7. THE Design_System SHALL prefer Server Components, marking components with 'use client' only when state, effects, or browser APIs are required.
8. THE Design_System SHALL NOT introduce new CSS frameworks, animation libraries, or icon sets beyond those specified in the tech stack steering document, while permitting Aceternity UI and Magic UI components copied into the repo for marketing surfaces only.
9. THE Design_System SHALL place page-specific components in `src/components/` and shared UI primitives in `src/components/ui/` following the project structure convention.
10. THE redesign SHALL preserve the existing route structure (`/`, `/login`, `/register`, `/upload`, `/matches/[id]`) and the existing security-headers proxy, CSRF handling, and authenticated/public route classifications, without introducing new routes for non-existent functionality.
11. THE redesign SHALL consume API request/response types from `packages/shared-types` (generated from the FastAPI OpenAPI schema) rather than hand-written types for match and resume shapes.

### Requirement 22: Design Review Gate

**User Story:** As the product owner, I want a mandatory design-artifact review before any implementation begins, so that the visual direction is validated and approved before code is written.

#### Acceptance Criteria

1. BEFORE any implementation work begins, THE redesign process SHALL produce a set of Design_Artifacts covering information architecture, UX design, and visual design.
2. THE information-architecture Design_Artifacts SHALL include a route map of the four MVP screens (`/`, auth, `/upload`, `/matches/[id]`) and the user-flow diagrams connecting them (Landing → Auth → Upload → Results).
3. THE UX-design Design_Artifacts SHALL include low-fidelity wireframes for each of the four MVP screens and a component inventory enumerating every reusable component required, each annotated with the backend fields (per Requirement 20) it consumes.
4. THE visual-design Design_Artifacts SHALL include high-fidelity mockups for each of the four MVP screens (with the ATS_Results_Page prioritized), plus documented color, typography, and spacing systems.
5. THE Design_Artifacts SHALL only reference functionality that exists in the MVP, with no wireframes or mockups for dashboards, match history, analytics, settings, pricing, or admin screens.
6. THE redesign process SHALL NOT begin implementation until the Design_Artifacts have been generated and explicitly approved.
7. THE high-fidelity mockup of the ATS_Results_Page SHALL demonstrate the measurable, screenshot-ready composition required by Requirement 14, using sample data conforming to the Requirement 20 contract.
