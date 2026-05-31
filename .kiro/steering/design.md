# MatchLayer — Design System

Always-loaded design baseline. Short and opinionated. Update when brand or design language changes.

## Visual reference points

The aesthetic is "modern AI-native SaaS": Linear, Vercel, Anthropic, Perplexity, Cursor, Raycast, v0, Resend, Cal.com, Modal. Common traits we adopt:

- Dark-first palette with seamless dark/light toggle.
- Heavy whitespace, restrained typography.
- Glass/frosted surfaces with backdrop blur for nav, cards, dialogs.
- Soft, far-spread shadows. No 2018-era boxy shadows.
- Subtle animations that reinforce hierarchy or feedback. Never decorative.
- Gradient accents (linear or conic) used sparingly, not splashed.
- Animated grids, dot patterns, or subtle noise in hero sections.
- High-contrast accent colors against neutral backgrounds.

## Brand tokens

### Color

Starting palette — refine in implementation if a specific shade looks off in context. Define as Tailwind theme colors, not magic strings in components.

| Token           | Light                    | Dark                  | Use                                       |
| --------------- | ------------------------ | --------------------- | ----------------------------------------- |
| `bg`            | `#FFFFFF`                | `#0A0A0B`             | Page background                           |
| `bg-elevated`   | `#F8F9FB`                | `#111114`             | Cards, panels                             |
| `bg-glass`      | `rgba(255,255,255,0.65)` | `rgba(20,20,24,0.55)` | Frosted nav, dialogs (with backdrop-blur) |
| `border`        | `#E5E7EB`                | `#1F1F23`             | Subtle dividers                           |
| `border-strong` | `#D1D5DB`                | `#2A2A30`             | Card borders, input borders               |
| `text`          | `#0A0A0B`                | `#F4F4F5`             | Primary text                              |
| `text-muted`    | `#52525B`                | `#A1A1AA`             | Secondary text                            |
| `text-subtle`   | `#71717A`                | `#71717A`             | Tertiary text, hints                      |
| `brand`         | `#7C3AED`                | `#8B5CF6`             | Primary brand (violet)                    |
| `brand-2`       | `#06B6D4`                | `#22D3EE`             | Secondary accent (cyan)                   |
| `success`       | `#10B981`                | `#34D399`             | Match found, positive signal              |
| `warning`       | `#F59E0B`                | `#FBBF24`             | Caution states                            |
| `danger`        | `#EF4444`                | `#F87171`             | Errors, destructive                       |

**Signature gradient:** `linear-gradient(135deg, var(--brand) 0%, var(--brand-2) 100%)` — violet → cyan. Used for: the score reveal moment, primary CTA hover, brand-mark fill. Use sparingly — gradient is a punctuation mark, not background paint.

### Typography

- **Sans:** **Geist Sans** via `next/font/google` (or `next/font` Geist if available). Variable font, optimized.
- **Mono:** **Geist Mono** for code, scores, identifiers, terminal output.
- **Display sizes:** rely on Tailwind's scale; cap at `text-6xl` (60px) for hero. Avoid heavier than `font-semibold` (600). Modern AI sites avoid black weights.
- **Letter-spacing:** tighten display text (`tracking-tight`); leave body at default.
- **Numerical features:** `font-variant-numeric: tabular-nums` for scores and tables.

### Spacing & layout

- 4px base grid. Tailwind defaults match this.
- Max content widths: `max-w-7xl` (1280px) for app shells, `max-w-3xl` (768px) for prose, `max-w-md` (448px) for auth forms.
- Vertical rhythm: section padding `py-24` on desktop landing, `py-16` mobile. Component padding `p-6` or `p-8` for cards.

### Radius

- `rounded-xl` (12px) default for cards, dialogs, buttons.
- `rounded-2xl` (16px) for hero cards and feature surfaces.
- `rounded-full` for badges and pills.
- No sharp corners (`rounded-none`) except code blocks.

### Shadows

- `shadow-sm` for resting cards.
- `shadow-lg` for elevated dialogs and popovers.
- Avoid Tailwind's default `shadow-md` — too 2018-era. Layer two shadows for depth: `shadow-[0_1px_2px_rgba(0,0,0,0.04),0_8px_24px_rgba(0,0,0,0.08)]`.

### Motion

Framer Motion is the standard. Principles:

- **Default duration:** 200ms for micro-interactions, 400ms for layout transitions, 600ms for hero reveals. Never longer.
- **Default easing:** `[0.16, 1, 0.3, 1]` (a smooth ease-out used by Framer Motion's `easeOut` curve, comparable to "easeOutExpo").
- **Respect `prefers-reduced-motion`** — wrap animations in a hook that disables motion when set.
- **Animations earn their place.** Loading state? Yes. Score reveal? Yes. Feature card on scroll? Only if it carries information.
- **No bounce on enter.** Use overshoot only on success states (e.g., "match found" check).

## Component sources

- **shadcn/ui** — base primitives (Button, Input, Dialog, etc). Copy in, don't dep on it. Source of truth for `apps/web/src/components/ui/`.
- **Aceternity UI** — pre-built fancy components (animated grid backgrounds, beam effects, sparkles, marquees, hover gradients). Copy in selectively. Use for landing/marketing surfaces only.
- **Magic UI** — alternative source for fancy components if Aceternity doesn't have what we need.
- **Lucide** — primary icon set (default for shadcn). Phosphor as fallback.
- **Tabler Icons** — only if Lucide doesn't have the icon needed.

## Where to be fancy vs calm

- **Marketing pages (`/`, `/pricing`, `/about`):** full polish. Animated grid in hero, scroll-driven section reveals, gradient text on key headlines, glass nav, subtle hover effects on feature cards.
- **Auth pages:** restrained. Centered card, brand mark at top, simple form. Background can have a subtle animated gradient or noise — nothing that competes with the form.
- **App shell (sidebar + main):** Linear/Notion calm. No animation noise. Fast feedback only.
- **Results page (the demo moment):** the score reveal deserves real attention. Animated count-up to the score, gradient on the score number, soft pulse on key skills matched. Charts for the breakdown should feel light — no chart-junk.
- **Settings, admin:** Vercel-style data tables and forms. Calm, fast, dense.

## Dark/light handling

- `next-themes` with `system` as the default. User can override.
- Tokens defined in `globals.css` via CSS custom properties; Tailwind reads them via `@theme inline`.
- Test every page in both themes before merging.

## Anti-patterns to refuse

- Animation as decoration. Every motion answers "what does this communicate?"
- Multiple competing accent colors. We have brand and brand-2; that's it.
- Gradient backgrounds on full sections. Gradients are punctuation, not paint.
- Drop-shadow stacking that hides the underlying surface.
- Inline styles, hex strings in components. Always use Tailwind tokens.
- Loading spinners as the only loading state. Use skeletons that match content shape.
- Toast-spam for success. Inline confirmations are calmer and more professional.
- Marketing gloss on the app side. The product is the hero, not the chrome.
- Custom font sizes off the type scale. If Tailwind's scale doesn't fit, the design is wrong, not the scale.
- More than 3 levels of visual hierarchy on any single screen.

## Accessibility

- Color contrast: WCAG AA minimum on all text. Test light and dark.
- Focus rings: visible, branded (use `brand`), never `outline: none` without a replacement.
- All interactive elements reachable by keyboard.
- shadcn primitives are accessible by default — don't break them.
- Motion respects `prefers-reduced-motion`.
- Form errors announced via `aria-live`.

## SEO & metadata (intersection with design)

See `seo.md` for the full policy. The design-relevant parts:

- **Semantic HTML is the shared currency of accessibility and SEO.** One `<h1>` per page, logical heading order, and landmark elements (`<header>`, `<nav>`, `<main>`, `<footer>`). The markup that helps screen readers is the same markup that helps crawlers.
- **Public vs. app.** Marketing pages (`/`, `/pricing`, `/about`) carry full metadata (title, description, Open Graph, canonical) via the Next.js Metadata API. App-shell pages are `noindex` — never add SEO chrome to them.
- **Images:** descriptive `alt` text on public pages; use `next/image` to protect Core Web Vitals (CLS/LCP).
- **OG images** follow the brand: the signature violet→cyan gradient and Geist type, so shared links look like MatchLayer.
