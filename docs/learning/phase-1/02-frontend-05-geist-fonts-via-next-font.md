# Geist Sans and Geist Mono via next/font

## Introduction

This document explains how a web framework loads custom fonts so that text
renders in the chosen typeface quickly and without the page jumping around. The
typefaces are Geist Sans and Geist Mono — a sans-serif family (letterforms with
no small finishing strokes, used here for ordinary text) and a monospaced family
(every character the same width, used for code, scores, and identifiers). The
loading mechanism is `next/font`, a built-in part of Next.js (a framework for
building web applications with React, the JavaScript library for building user
interfaces) that handles fonts at build time rather than fetching them from a
third party while the page loads.

**Learning outcomes** — after reading this document you will be able to:

- Explain why custom web fonts can slow a page down and cause visible text shifting. The browser must obtain the font file before it can paint text in that typeface.
- Describe what the framework's font loader does to avoid that: it prepares the font at build time and serves it from the same origin as the page. Self-hosting the font removes the third-party round trip.
- Read the code that loads two font families and exposes them to the stylesheet as named variables. Each loaded font becomes a custom property the styles can reference.
- Recognise the common mistakes around font loading and recover from them. Most issues trace back to how the font is requested or where its variable is applied.

Prerequisites: this document builds on
[The Next.js App Router and React Server Components](02-frontend-01-nextjs-app-router-and-rsc.md),
which introduces the root layout file where the fonts are loaded, and
[Tailwind CSS v4 and the theme-token strategy](02-frontend-03-tailwind-v4-and-theme-tokens.md),
which introduces the design tokens and the theme block that binds these font
variables to the text utilities.

## Problem it solves

A page that uses a custom typeface has to get the font file to the browser before
it can draw text in that typeface. That single fact creates two concrete
problems.

The first is a slow, third-party round trip. The common prior approach linked the
font from an external font-hosting service. The browser had to open a connection
to that other origin, download the font, and only then paint the text — a delay
on every fresh visit, and a dependency on a server you do not control. It also
shares visitor information with that third party, which is a privacy concern.

The second is layout shift. While the custom font is still loading, the browser
either shows nothing (a flash of invisible text) or shows a fallback font and
then re-renders when the custom one arrives (a flash of unstyled text). When the
fallback and the custom font differ in size, the surrounding content jumps when
the swap happens — a jarring, measurable quality problem.

The framework's font loader addresses both. It downloads and processes the font
at build time and serves it from the same origin as the page, removing the
third-party round trip and its privacy cost. And it controls how the swap behaves
so text stays visible during loading, reducing the jump.

## Mental model

Think of a custom font like a special ink a print shop needs to produce a poster.
The slow way is to phone another supplier for the ink each time a poster is
ordered and wait for delivery before printing. The font loader is like buying the
ink in advance and keeping it on the shelf: when an order comes in, the ink is
already there, so printing starts immediately.

When the framework loads a font this way, the sequence is:

1. At build time, the loader obtains the font file and bundles it with the application's own assets, so it ships from the same origin as the page.
2. For each font you load, the loader produces a named handle that carries a CSS variable name — a custom property the stylesheet can read.
3. You attach those variable names to the page's root element so the variables are available to every element below.
4. The styling layer binds its text utilities (the "use the sans font", "use the mono font" classes) to those variables, so text picks up the right family.
5. When the page renders, the font is already served locally and the chosen display behaviour keeps text visible while it settles, so there is little or no jump.

Steps 2 and 3 are the connective tissue: each loaded font becomes a named
variable, and applying that variable to the root makes the font usable everywhere.

## How it works

Rendering text in a custom typeface requires the matching font file to be
available to the browser. A font loader built into a web framework moves that
work to build time. Instead of the page asking a third-party service for the font
while it loads, the loader fetches the font during the build, optimises it, and
stores it among the application's own static assets so it is served from the same
origin as the page. Same-origin serving removes a separate network connection and
keeps visitor data from being shared with an outside font host.

For each font family you load, the loader returns a handle. Rather than forcing
you to hard-code a generated class name, the loader can expose the font as a CSS
variable — a named value declared in the stylesheet and read elsewhere. You give
the loader a variable name, it binds the font to that variable, and you attach the
variable to the page's root element. Every descendant element can then resolve
that variable, so any part of the styling system can say "use this variable's
font" without knowing the font's internal details.

The loader also controls the _display_ behaviour — what the browser shows while
the font is still settling. A common, recommended choice keeps text visible using
a fallback face and then swaps in the custom face when it is ready, so the reader
never stares at invisible text. Pairing that with same-origin serving (so the
font is available almost immediately) keeps any visible swap small.

Two families are loaded the same way independently — one sans-serif for body
text, one monospaced for code and figures — each producing its own variable. The
styling layer then binds its "sans" text utility to the sans variable and its
"mono" text utility to the mono variable, so choosing a typeface in markup is a
matter of which utility class an element carries, and the underlying font flows
from the variable set on the root element.

## MatchLayer Phase 1 usage

In MatchLayer both font families are loaded in the root layout file,
`apps/web/src/app/layout.tsx`, using the framework's built-in font loader. Each
call names a subset of characters to include, a CSS variable to expose the font
under, and the display behaviour that keeps text visible while the font settles:

Source: `apps/web/src/app/layout.tsx`

```tsx
const geistSans = Geist({
  subsets: ["latin"],
  variable: "--font-geist-sans",
  display: "swap",
});

const geistMono = Geist_Mono({
  subsets: ["latin"],
  variable: "--font-geist-mono",
  display: "swap",
});
```

The `variable` field is the connective piece: it binds Geist Sans to the
`--font-geist-sans` custom property and Geist Mono to `--font-geist-mono`. The
`display: "swap"` field is the display behaviour that keeps text visible during
loading.

The layout then attaches both variables to the page's root element by putting
the handles' generated class names on `<html>`, which makes the two custom
properties available to every element on every page:

Source: `apps/web/src/app/layout.tsx`

```tsx
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable}`}
    >
```

From there the styling layer binds its `font-sans` and `font-mono` text
utilities to those two variables in the stylesheet's theme block, so an element
gets Geist Sans or Geist Mono purely from the utility class it carries, with the
font flowing from the variables set here on `<html>`.

## Common pitfalls

- **Mistake:** Loading the font from an external font-hosting service by hand instead of using the framework's build-time loader.
  **Symptom:** Every fresh visit pays a separate network round trip to the third-party host before text paints, and visitor data is shared with that host.
  **Recovery:** Load the font through the framework's font loader so it is bundled and served from the same origin, removing the extra connection and the privacy cost.

- **Mistake:** Loading a font under a CSS variable but never attaching that variable to the root element.
  **Symptom:** Text falls back to a default system font even though the font was loaded, because no element exposes the variable for the styling utilities to read.
  **Recovery:** Apply the loader's generated class names (which carry the variables) to the page's root element so the variables are available to every descendant.

- **Mistake:** Choosing a display behaviour that hides text until the custom font is fully ready.
  **Symptom:** The page shows a stretch of invisible text on load, then the words appear once the font arrives.
  **Recovery:** Use a display behaviour that keeps text visible with a fallback face and swaps the custom face in when ready, so readers never face blank text.

- **Mistake:** Requesting far more of the font than the page needs — every character range or weight — when only a small subset is used.
  **Symptom:** The bundled font assets are larger than necessary, slowing the load they were meant to speed up.
  **Recovery:** Limit the loaded character subset to what the application actually renders so the served font stays small.

## External reading

- [Next.js: Font optimization with next/font](https://nextjs.org/docs/app/getting-started/fonts)
- [Next.js: next/font API reference](https://nextjs.org/docs/app/api-reference/components/font)
- [MDN Web Docs: the CSS font-display descriptor](https://developer.mozilla.org/en-US/docs/Web/CSS/@font-face/font-display)
- [MDN Web Docs: web fonts](https://developer.mozilla.org/en-US/docs/Learn_web_development/Core/Text_styling/Web_fonts)
