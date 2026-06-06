# shadcn/ui as a copy-in primitive library

## Introduction

This document explains an unusual way to use a component library — a collection
of ready-made interface building blocks such as buttons, inputs, and dialogs.
The library is shadcn/ui, and its defining idea is that you do not install it as
a dependency that sits hidden inside your project's downloaded packages. Instead
you _copy its source files into your own repository_ and own them like code you
wrote. A primitive here means a small, low-level building block (a button, a text
input) that more complex components are assembled from. This document shows how
the project records its setup choices and what a copied-in primitive looks like
once it lives in the codebase.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a copy-in component library is and how it differs from a normal installed dependency. The source lives in your repository, not in a downloaded package folder.
- Describe the trade-off the copy-in model makes: full control and direct edits in exchange for owning the maintenance. You gain the ability to change anything and take on keeping it current.
- Read a configuration file that records the library's setup choices and read a copied-in primitive's source. The configuration captures style and path decisions so generated files land consistently.
- Recognise the common mistakes around copy-in primitives and recover from them. Most confusion comes from treating copied files as untouchable vendor code.

Prerequisites: this document builds on
[The Next.js App Router and React Server Components](02-frontend-01-nextjs-app-router-and-rsc.md),
which introduces the Server Component versus Client Component split the
primitives are built within, and
[Tailwind CSS v4 and the theme-token strategy](02-frontend-03-tailwind-v4-and-theme-tokens.md),
which introduces the utility classes and design tokens the primitives are styled
with.

## Problem it solves

An application needs accessible, well-built interface components — a button that
handles focus correctly, an input that behaves with assistive technology — and
building them all from scratch is slow and error-prone. The straightforward
answer is to install a component library as a dependency. That solves the
build-it-yourself problem but introduces two new ones.

The first is the customisation wall. When a library is an installed dependency,
its components live in a read-only package folder. To change how a button looks
or behaves beyond the options the library exposes, you have to fight the
library's theming layer or wrap and override it, because you cannot edit the
source directly.

The second is version coupling and lock-in. An installed component library
upgrades as one unit; a new major version can change many components at once, and
you take the whole change or none of it. Your design becomes shaped by what the
library makes easy to override.

The copy-in model addresses both by changing where the source lives. The
components are copied into your repository as ordinary source files you own. You
can edit any line directly, the components are styled with your own design
tokens, and there is no library version to track — but you accept responsibility
for maintaining the copied code.

## Mental model

Think of the difference between renting furniture and buying the raw lumber and a
set of plans. A normal installed library is rented furniture: it arrives
assembled, you arrange it, but you cannot saw a leg off without breaking the
rental terms. The copy-in model hands you the lumber and the plans — you build
the piece into your own house, and from then on it is yours to modify, repair, or
rebuild.

When you add a copy-in primitive to a project, the flow is:

1. You run the library's command-line tool and name the primitive you want, for example a button.
2. The tool reads a small configuration file in your repository that records your setup choices — the visual style, where components go, which utility helpers and icon set you use.
3. The tool writes the primitive's source code directly into your chosen folder as a normal file in your repository.
4. From that point the file is yours: it is committed with your code, edited like your code, and styled with your own tokens.
5. Updating a primitive later means re-running the tool or editing the file by hand — there is no package upgrade step, because nothing was installed.

Step 3 is the whole distinction: the component becomes a file in your repository
rather than an entry in a downloaded package folder.

## How it works

A typical component library is published as a package, downloaded into a
project's dependency folder, and imported from there. Its source is read-only in
practice — you are expected to configure it through the options it exposes, not
to edit it. Upgrades come by bumping the package version.

A copy-in library inverts that. It is distributed as source you generate into
your own tree using a command-line tool. The tool relies on a configuration file
committed in the repository that records the project's choices so generated
components are consistent: which visual style variant to use, whether components
are written for the server-rendering model, where component files and shared
helper utilities live, and which icon set to assume. Because those answers are
written down, every primitive the tool generates lands in the right place and is
wired to the same helpers.

A generated primitive is plain component source. It usually leans on a couple of
small, genuinely installed helper packages — for example a utility that merges
class-name strings, or one that expresses style variants as data — but the
primitive's own markup, class names, and structure are right there in your file.
That is what makes deep customisation trivial: to change a button's default
color or to remap it onto your design tokens, you edit the file directly, the
same way you would edit any component you wrote.

The cost side is real and worth stating plainly. Because nothing is installed for
these components, nothing updates them for you. If the upstream project fixes a
bug or improves accessibility in a primitive, you do not get it automatically;
you re-generate or hand-apply the change. The benefit — total control and styling
in your own tokens — is paid for with ownership of maintenance.

## MatchLayer Phase 1 usage

In MatchLayer the copy-in setup choices are recorded in `apps/web/components.json`
at the root of the web application. The tool reads this file so every primitive
it generates uses the same style, target model, paths, and icon set:

Source: `apps/web/components.json`

```json
{
  "style": "new-york",
  "rsc": true,
  "tsx": true,
  "tailwind": {
    "config": "",
    "css": "src/app/globals.css",
    "baseColor": "neutral",
    "cssVariables": true,
    "prefix": ""
  },
  "aliases": {
    "components": "@/components",
    "ui": "@/components/ui",
    "utils": "@/lib/utils",
    "lib": "@/lib",
    "hooks": "@/hooks"
  },
  "iconLibrary": "lucide"
}
```

Reading the fields: `style` picks the visual variant; `rsc` records that
components target the server-rendering model; the `tailwind` block points at the
project stylesheet that holds the design tokens; the `aliases` block says where
generated component and helper files go; and `iconLibrary` fixes the icon set.

A copied-in primitive then lives as ordinary source. The button primitive is at
`apps/web/src/components/ui/button.tsx`, and because the project owns the file it
has been edited to drop the library's default color names and resolve everything
against MatchLayer's own brand tokens. The default style variant shows this
directly:

Source: `apps/web/src/components/ui/button.tsx`

```tsx
      variant: {
        default: "bg-brand text-white shadow-sm hover:bg-brand/90",
        destructive: "bg-danger text-white shadow-sm hover:bg-danger/90",
```

Those `bg-brand` and `bg-danger` classes are the project's own design tokens,
written straight into the primitive — an edit that is only possible because the
file is owned source rather than read-only package code.

## Common pitfalls

- **Mistake:** Treating a copied-in primitive as untouchable vendor code and wrapping it in extra layers to restyle it instead of editing the file.
  **Symptom:** The component gains needless wrapper components and overrides, and its styling fights itself, when a direct edit to the owned file would have been simpler.
  **Recovery:** Edit the primitive's source directly — that ownership is the whole point of the copy-in model — and keep the change committed with the rest of your code.

- **Mistake:** Expecting copied-in primitives to update automatically when the upstream project improves them.
  **Symptom:** A known upstream fix or accessibility improvement never appears in your component, because nothing installed manages it.
  **Recovery:** Track upstream changes deliberately and re-generate or hand-apply the ones you want, accepting that maintenance is yours under this model.

- **Mistake:** Generating a primitive without the setup configuration in place, or with the wrong paths recorded in it.
  **Symptom:** The generated file lands in the wrong folder or is wired to helpers that do not exist, and the import paths break.
  **Recovery:** Make sure the configuration file records the correct style, paths, and helpers before generating, then re-generate so the primitive lands where the rest of the code expects it.

- **Mistake:** Leaving the library's default color names in a generated primitive instead of mapping them to the project's design tokens.
  **Symptom:** The component renders with colors that ignore the theme and do not match the rest of the interface, and it does not follow light and dark switches.
  **Recovery:** Replace the default color classes with the project's token-backed utility classes in the owned file so the primitive matches the design system and themes correctly.

## External reading

- [shadcn/ui: introduction and philosophy](https://ui.shadcn.com/docs)
- [shadcn/ui: the components.json configuration](https://ui.shadcn.com/docs/components-json)
- [shadcn/ui: the Button component](https://ui.shadcn.com/docs/components/button)
- [Tailwind CSS: styling with utility classes](https://tailwindcss.com/docs/styling-with-utility-classes)
