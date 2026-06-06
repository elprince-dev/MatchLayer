# next-themes and the system-default theme

## Introduction

This document explains how a web application lets a reader switch between a
light appearance and a dark appearance, remembers their choice, and decides what
to show on a brand-new visit before anyone has chosen anything. The tool is
`next-themes` (a small library — a reusable package of code you import rather
than write — for managing light and dark appearance in a Next.js application,
where Next.js is a framework for building web applications with React, the
JavaScript library for building user interfaces). The phrase "system default"
refers to one of the choices this tool offers: following the reader's operating
system preference for light or dark rather than forcing one. This document also
shows the deliberate alternative the project actually ships, where a fixed
default is pinned instead.

**Learning outcomes** — after reading this document you will be able to:

- Explain how an application stores and applies a light-or-dark appearance choice. The choice is saved on the device and re-applied on the next visit.
- Describe what "follow the system preference" means and how it differs from pinning a fixed default. One tracks the operating system; the other ignores it.
- Read the code that configures the theme tool and identify which appearance is the default and whether the system option is enabled. The configuration props state both directly.
- Recognise the common mistakes around theme management and recover from them. Most defects show up as a brief flash of the wrong appearance on load.

Prerequisites: this document builds on
[The Next.js App Router and React Server Components](02-frontend-01-nextjs-app-router-and-rsc.md),
which introduces the root layout where the theme provider is mounted and the
server-versus-browser rendering split this tool has to work around. It also
relates to
[Tailwind CSS v4 and the theme-token strategy](02-frontend-03-tailwind-v4-and-theme-tokens.md),
which defines the light and dark color values this tool switches between by
toggling a class on the page's root element.

## Problem it solves

A modern application is usually expected to offer both a light and a dark
appearance. That expectation creates three concrete problems. First, the chosen
appearance has to persist — a reader who picks dark should still see dark on
their next visit, not be reset. Second, a first-time visitor has no stored
choice, so the application must decide what to show them. Third, and most
subtly, the correct appearance has to be applied before the page first paints,
or the reader sees a brief flash of the wrong colours as it corrects itself.

The common prior approach hand-rolled this: a little script saved a value in the
browser, another read it on load and toggled a class, and the application hoped
the timing worked out. That hand-rolled approach routinely produced the flash
problem, because the correcting script ran after the first paint, and it rarely
handled the "follow the operating system" case at all.

A dedicated theme library solves all three. It persists the choice, it lets the
application decide the first-visit default (a fixed appearance, or "follow the
system"), and it injects a tiny script that applies the resolved appearance
before the first paint so there is no flash.

## Mental model

Think of the theme tool as a coat-check attendant for the page's appearance. On
the way in, the attendant checks whether you left a coat last time (a stored
choice). If you did, you get it back immediately. If you did not, the attendant
follows the house rule — either "hand everyone the dark coat by default" or
"match the coat to the weather outside" (the operating system preference).

When the page loads, the resolution runs in this order:

1. The tool looks for a previously stored appearance choice for this reader on this device.
2. If a stored choice exists, that appearance wins and is applied.
3. If there is no stored choice, the tool falls back to the configured default — either a fixed appearance the application pins, or the operating system's light-or-dark preference when the system option is enabled.
4. The tool applies the resolved appearance by toggling a marker class on the page's root element before the first paint, so the very first frame is already correct.
5. When the reader later changes the appearance, the tool updates the stored choice and re-toggles the class, and the change takes effect immediately.

Step 3 is where the "system default" choice lives: it is the rule the
attendant follows when you arrive without a coat.

## How it works

A theme-management library coordinates three things: where the appearance choice
is stored, how it is applied to the page, and what to do when no choice exists
yet.

Storage is the reader's own device. The library saves the selected appearance
locally so it survives reloads and return visits, scoped to that browser. Nothing
is sent to a server, so the preference is private to the device.

Application is done by toggling a class — a named marker — on the page's root
element. The styling layer defines one set of colours that applies normally and
another set that applies only when that class is present, so flipping the class
flips the whole palette. This is why the library only has to add or remove one
class to change the entire appearance.

The first-visit decision is the configurable part. The library accepts a default
appearance and a switch for whether to follow the operating system. When the
system option is on, a fresh visitor sees light or dark according to their
operating system's own preference, detected through a browser media query (a
conditional styling rule that reacts to an environment condition such as the
system's dark-mode setting). When the system option is off, the library ignores
the operating system entirely and shows the fixed default the application pinned
until the reader chooses otherwise.

The flash-free behaviour comes from a small inline script the library injects to
run before the page's first paint. Because a framework that renders pages on the
server cannot know the device's stored choice or operating-system preference
while rendering, that knowledge only exists in the browser. The pre-paint script
reads the stored choice (or the system preference) and sets the marker class
immediately, so the first painted frame already matches — no wrong-appearance
flash. The trade-off is that this script intentionally mutates the page's root
element before the rendering framework reconciles it, so the application must
tell the framework to tolerate that one mismatch rather than warn about it.

## MatchLayer Phase 1 usage

In MatchLayer the theme tool is configured in a thin wrapper component,
`apps/web/src/components/theme-provider.tsx`, which re-exports the library's
provider with the project's chosen defaults. It imports the library's provider:

Source: `apps/web/src/components/theme-provider.tsx`

```tsx
import {
  ThemeProvider as NextThemesProvider,
  type ThemeProviderProps,
} from "next-themes";
```

The wrapper pins the project's decisions through the provider's configuration
props:

Source: `apps/web/src/components/theme-provider.tsx`

```tsx
export function ThemeProvider({ children, ...props }: ThemeProviderProps) {
  return (
    <NextThemesProvider
      attribute="class"
      defaultTheme="dark"
      enableSystem={false}
      disableTransitionOnChange
      {...props}
    >
      {children}
    </NextThemesProvider>
  );
}
```

Reading the props: `attribute="class"` tells the tool to toggle a `dark` class
on the root element (which the stylesheet reads to swap the colour set);
`defaultTheme="dark"` pins dark as the first-visit default; and
`enableSystem={false}` turns the "follow the operating system" option off. So
although this topic is named for the system-default behaviour, MatchLayer
deliberately does **not** follow the system on a fresh visit — it shows dark by
default and lets the reader switch — which is a clear example of choosing a
fixed default over the system option. The pre-paint script still applies the
stored choice (or that dark default) before first paint, which is why the root
layout marks the root element with `suppressHydrationWarning` to tolerate the
pre-paint mutation:

Source: `apps/web/src/app/layout.tsx`

```tsx
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable}`}
    >
```

## Common pitfalls

- **Mistake:** Configuring the theme tool but never marking the page's root element to tolerate the pre-paint script's mutation.
  **Symptom:** The browser console fills with hydration-mismatch warnings on every load, because the framework rendered one appearance on the server and the pre-paint script changed it in the browser.
  **Recovery:** Add the suppress-hydration-warning marker to the root element so the framework accepts the deliberate pre-paint change without warning.

- **Mistake:** Applying the saved appearance only after the page has already painted, with a hand-rolled script instead of a pre-paint one.
  **Symptom:** A visible flash of the wrong appearance on load — the page paints light, then snaps to dark a moment later.
  **Recovery:** Use the library's pre-paint injection so the correct appearance is set before the first frame, eliminating the flash.

- **Mistake:** Expecting a fresh visitor to follow their operating system preference while the system option is turned off.
  **Symptom:** A reader whose device is set to dark still sees the pinned default on their first visit, which feels like the setting is being ignored.
  **Recovery:** Decide deliberately: enable the system option if first-visit appearance should track the operating system, or document that a fixed default is intended and the reader can switch manually.

- **Mistake:** Defining the dark colour set under a class but choosing a class-name that does not match the one the theme tool toggles.
  **Symptom:** Switching the appearance updates the marker class but nothing visually changes, because the styling layer is keyed to a different class name.
  **Recovery:** Align the styling layer's dark selector with the exact class the tool toggles, so flipping the class flips the palette.

## External reading

- [next-themes (project documentation and configuration)](https://github.com/pacocoursey/next-themes)
- [MDN Web Docs: prefers-color-scheme media feature](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-color-scheme)
- [MDN Web Docs: Window.localStorage](https://developer.mozilla.org/en-US/docs/Web/API/Window/localStorage)
