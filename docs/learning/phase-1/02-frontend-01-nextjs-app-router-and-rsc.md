# The Next.js App Router and React Server Components

## Introduction

This document explains how a modern web framework decides which page to show
for a given web address, and where the code for that page runs. The framework
is Next.js (a framework for building web applications with React, the JavaScript
library for building user interfaces). Its routing system — the part that maps a
web address to the code that renders it — is called the App Router, because the
routes are described by the folder layout inside a directory named `app`. The
App Router also introduces a split that is the heart of this topic: a Server
Component (a piece of user-interface code that runs only on the server and sends
finished markup to the browser) versus a Client Component (a piece of
user-interface code that is also sent to the browser so it can respond to clicks,
typing, and other interaction there).

**Learning outcomes** — after reading this document you will be able to:

- Explain how the App Router turns a folder of files into the set of web addresses an application answers. This is the routing idea, expressed as a directory layout rather than a configuration file.
- Describe the difference between a Server Component and a Client Component, and state which one is the default. Knowing the default is what keeps the amount of code shipped to the browser small.
- Read a root layout file and identify where shared structure and providers wrap every page. The root layout is the single wrapper that every route renders inside.
- Recognise the common mistakes around the server/client boundary and recover from them. Most early bugs in this model come from misplacing that boundary.

Prerequisites: this document builds on
[Monorepo layout and the apps-vs-packages split](01-foundations-01-monorepo-layout.md), which
introduces the single-repository layout the web application lives in. No other
prerequisites; the styling, font, and theming topics that the example layout
also wires up are covered in their own documents in this track.

## Problem it solves

A web application has to answer many different web addresses — a home page, a
sign-in page, a results page — and for each one it has to decide what to render.
It also has to decide _where_ the rendering happens: entirely in the browser,
entirely on the server, or split between them. Two concrete problems follow from
those decisions.

The first problem is routing sprawl. The common prior approach kept a central
routing table — one file that listed every address and the component it mapped
to. As an application grew, that table became a bottleneck every contributor had
to edit, and it drifted out of step with the actual files on disk.

The second problem is shipping too much code to the browser. An earlier, widely
used approach rendered almost everything in the browser: the server sent a nearly
empty page plus a large bundle of JavaScript, and the browser built the page
from scratch. That made the first view slow and forced even purely static,
non-interactive content to arrive as executable code the browser had to download
and run.

The App Router addresses the first problem by deriving routes from the folder
layout, so the files on disk _are_ the routing table. It addresses the second by
making server-side rendering the default through Server Components, so only the
genuinely interactive parts are sent to the browser as code.

## Mental model

Think of the `app` directory as a building with rooms, where the path of folders
to a room is the address you type to reach it. A folder named `login` holds the
sign-in room; the file inside it that the framework recognises as a page is what
visitors actually see when they arrive at `/login`. You do not write a separate
list of which address leads to which room — the corridors (the folder nesting)
already encode it.

When a request for a page arrives, the framework resolves it like this:

1. It reads the requested address and walks the matching chain of folders inside the `app` directory, from the outermost down to the target page.
2. It renders the shared layout files it meets along the way, from the outside in, so each one wraps the content below it.
3. It renders the target page. By default this happens on the server, producing finished markup rather than browser code.
4. For any component explicitly marked to run in the browser, it sends that component's code to the browser and "hydrates" it — attaches the page's event handlers so clicks and typing start working.
5. The browser receives ready-to-display markup immediately, and the interactive pieces come alive once their code loads.

That order — addresses come from folders, layouts wrap from the outside in, the
server renders by default, and only marked components ship to the browser — is
the whole model.

## How it works

A routing system maps an incoming web address to the code that produces a
response. A file-based router does this by convention: the structure of the
folders and files on disk defines the addresses, so adding a folder adds a route
without editing any central list. Special filenames carry meaning — one filename
marks the visible page for a route, another marks a layout that wraps that page
and everything nested below it. Because layouts nest, shared structure (a
navigation bar, a footer, a set of providers) is written once at the level it
applies to and inherited by every route underneath.

The deeper idea is _where_ a component runs. In this model the default is the
server. A Server Component executes only on the server during the request: it can
read data directly, and it emits finished markup. Its code is never sent to the
browser, so it adds nothing to the amount of code the visitor downloads. The
trade-off is that a Server Component cannot do anything that requires the
browser — it has no access to browser-only features, cannot hold interactive
state, and cannot respond to events.

A Client Component is the opposite trade. It is explicitly opted in with a marker
at the top of the file. Its code is sent to the browser, where it is "hydrated" —
the framework attaches event handlers to the already-rendered markup so it
becomes interactive. A Client Component can hold state, run effects, and read
browser-only features, at the cost of adding its code to the browser bundle.

The two compose. A Server Component can render a Client Component inside it, so a
mostly static page can contain small interactive islands. The practical rule that
falls out of this is to keep components on the server by default and reach for a
Client Component only at the points that genuinely need interaction, which keeps
the downloaded code small while leaving the interactive parts fully alive.

## MatchLayer Phase 1 usage

In MatchLayer the web application uses the App Router, and its single root
layout lives at `apps/web/src/app/layout.tsx`. This file is the outermost wrapper
every route renders inside: it imports the global stylesheet, loads the fonts,
and wraps all pages in the shared providers. The function that defines the
wrapper is:

Source: `apps/web/src/app/layout.tsx`

```tsx
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}): React.JSX.Element {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className={`${geistSans.variable} ${geistMono.variable}`}
    >
      <body className="bg-bg text-text font-sans antialiased">
        <Providers>
          <ThemeProvider>{children}</ThemeProvider>
        </Providers>
      </body>
    </html>
  );
}
```

The `children` parameter is the route being rendered; the layout draws the
`<html>` and `<body>` shell around it once, and every page in the application
appears in that `children` slot. The layout itself ships no `'use client'`
marker, so it is a Server Component — it renders on the server and adds no code
of its own to the browser bundle.

The public landing page at `apps/web/src/app/(marketing)/page.tsx` is also a
Server Component, and being a Server Component is exactly what lets it export the
page's metadata:

Source: `apps/web/src/app/(marketing)/page.tsx`

```tsx
export const metadata: Metadata = buildMarketingMetadata({ path: "/" });
```

That page composes small interactive islands — the navigation bar and the
animated hero are Client Components marked with `'use client'` — inside an
otherwise server-rendered page, which is the compose-the-two pattern the model
encourages.

## Common pitfalls

- **Mistake:** Using a browser-only feature (interactive state, an effect, a click handler, or a `window` reference) in a component that has no `'use client'` marker, so it is still a Server Component.
  **Symptom:** The build or the server render fails with an error stating that a hook or browser-only capability is not available in a Server Component.
  **Recovery:** Add the `'use client'` marker at the very top of that component's file, or move only the interactive part into a separate Client Component and keep the rest on the server.

- **Mistake:** Marking a large, high-level component `'use client'` to fix one small interactive piece, which turns the whole subtree into browser code.
  **Symptom:** The browser bundle grows and the first view slows down, because far more code than necessary is now shipped and hydrated.
  **Recovery:** Push the `'use client'` marker down to the smallest component that truly needs interaction, leaving its parents as Server Components that render markup only.

- **Mistake:** Exporting page metadata from a component that is a Client Component.
  **Symptom:** The metadata export is ignored and the page renders without the title or description you expected, because only Server Components may export metadata.
  **Recovery:** Keep the page itself a Server Component that exports the metadata, and render any interactive parts as Client Components nested inside it.

- **Mistake:** Putting page-specific content directly in a layout file instead of in the page file.
  **Symptom:** The same content appears on every route nested under that layout, because a layout wraps all of them rather than rendering for one address only.
  **Recovery:** Move route-specific content into the page file for that route and keep the layout limited to structure shared by every nested route.

## External reading

- [Next.js: Routing fundamentals (App Router)](https://nextjs.org/docs/app/building-your-application/routing)
- [Next.js: Server and Client Components](https://nextjs.org/docs/app/getting-started/server-and-client-components)
- [Next.js: pages and layouts](https://nextjs.org/docs/app/api-reference/file-conventions/layout)
- [MDN Web Docs: client-side versus server-side rendering](https://developer.mozilla.org/en-US/docs/Web/Performance/Guides/Rendering)
