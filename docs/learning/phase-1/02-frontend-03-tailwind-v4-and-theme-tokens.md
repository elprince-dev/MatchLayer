# Tailwind CSS v4 and the theme-token strategy

## Introduction

This document explains how a styling tool lets you build an interface by writing
small, single-purpose class names directly in your markup, and how the project
feeds its brand colors into that tool from one place. The tool is Tailwind CSS
(a utility-first styling framework — "utility-first" means you compose a design
from many tiny classes that each set one property, like one class for a
background color and another for padding, instead of writing custom rules for
each component). The project is on Tailwind version 4, whose configuration lives
in the stylesheet itself. The mechanism that connects the project's colors to
Tailwind's classes is a block written `@theme inline`, where a design token (a
named value such as a brand color, stored once and referred to everywhere) is
declared so the styling classes resolve to it.

**Learning outcomes** — after reading this document you will be able to:

- Explain what utility-first styling is and why a design token feeds many utility classes from one definition. A token defined once flows into every class that reads it.
- Describe what changed in version 4: configuration moves into the stylesheet rather than a separate file. The stylesheet becomes the single place the theme is declared.
- Read a stylesheet that defines color tokens for light and dark and re-exports them through a theme block. The same token name carries a different value per theme.
- Recognise the common mistakes around the token strategy and recover from them. Most issues come from a token and its re-export drifting apart.

Prerequisites: this document builds on
[Monorepo layout and the apps-vs-packages split](01-foundations-01-monorepo-layout.md), which
introduces the single-repository layout the stylesheet lives in. A dark-and-light
theme switch is covered separately in
[next-themes and the system-default theme](02-frontend-07-next-themes-and-system-default.md);
this document only needs the idea that a class on the page's root element selects
which set of token values is active.

## Problem it solves

An interface needs its colors, spacing, and type to be consistent across every
screen, and it needs to change in one edit when the brand evolves. Two concrete
problems get in the way.

The first is scattered values. The common prior approach hard-coded color codes
and pixel measurements directly into each component's styles. When the brand
color changed, someone had to find and update every copy, and the copies drifted,
so the same "brand purple" slowly became three slightly different purples.

The second is a separate, growing configuration file. Earlier versions of this
kind of tool kept the theme in a standalone configuration file written in a
programming language, separate from the stylesheet. That meant the design values
lived in one file, the styles in another, and keeping them in step was manual.

The token strategy on Tailwind version 4 addresses both. Each design value is
declared once as a named token, so changing the brand color is a single edit that
flows everywhere. And the theme is declared inside the stylesheet itself, so the
values and the styles that consume them live together in one file.

## Mental model

Think of the design tokens as the labelled paint pots in a workshop — "brand",
"background", "text" — and the utility classes as instructions that say "paint
this with the brand pot" without naming the actual color. Swap what is in the
"brand" pot and every instruction that referenced it produces the new color, with
no instruction rewritten. Switching from light to dark is swapping the whole set
of pots for a darker set while keeping the same labels.

When the page renders a styled element, the chain works like this:

1. The stylesheet declares each token as a named value — for example a `brand` color — once for the light set and once for the dark set.
2. A theme block re-exports each token under the name the utility classes expect, so a class like "background is brand" knows where to read its value.
3. The markup carries small utility classes that reference tokens by name rather than by raw color.
4. A class on the page's root element selects which set of token values — light or dark — is currently active.
5. The browser paints each element by resolving its utility classes to the active token values, so one element's color follows whichever set is active.

Steps 1 and 2 are the strategy's core: define the value once, then re-export it
so the utility classes can find it.

## How it works

A utility-first styling framework provides a large set of tiny classes, each
setting exactly one style property to one value. You build a component by
combining these classes in the markup instead of writing separate style rules.
For colors specifically, the framework exposes color utilities — set a
background, set the text color, set a border — and each utility needs to know
which named colors exist.

The framework lets a project define those named colors as theme values. In
version 4 of this kind of framework, that definition moved out of a separate
configuration file and into the stylesheet, inside a dedicated theme block. A
value declared in that block becomes available to the matching utility classes:
declare a color named `brand`, and the framework generates the "background is
brand", "text is brand", and "border is brand" utilities automatically.

The recommended way to make those values themeable — different in light and dark —
is a two-layer arrangement built on custom properties. A custom property is a
named value declared in the stylesheet (written with a leading double-hyphen) and
read elsewhere. The first layer declares each token as a plain custom property,
and declares it twice: once under the document root for the light set, and once
under a class selector for the dark set, so the same property name holds a
different value depending on which selector is active. The second layer is the
theme block, which re-exports each of those custom properties as a theme color.
The utility classes resolve to the theme color, the theme color points back to the
custom property, and the custom property's value depends on whether the
dark-selecting class is present on the root element. The result: one element's
utility class produces the light value or the dark value with no change to the
markup.

A subtlety worth holding onto is that the colors are stored as their raw
channel components rather than as a finished color string. Storing the raw
channels lets the framework apply opacity — "this color at sixty percent" —
by mixing, which is why a token is often written as three numbers rather than a
single color code.

## MatchLayer Phase 1 usage

In MatchLayer the global stylesheet is `apps/web/src/app/globals.css`. It pulls
in the framework, declares a dark variant tied to a class, and defines the color
tokens as raw red-green-blue channel triplets for the light set on the document
root:

Source: `apps/web/src/app/globals.css`

```text
@import "tailwindcss";
```

The light token set lives under the document root, where each color is stored as
its three channel numbers:

Source: `apps/web/src/app/globals.css`

```text
:root {
  /* Surfaces */
  --color-bg: 255 255 255;
  --color-bg-elevated: 248 249 251;
  --color-bg-glass: 255 255 255;
```

The dark set re-declares the same token names under a `.dark` class selector, so
the very same name resolves to a darker value whenever that class is present on
the page's root element:

Source: `apps/web/src/app/globals.css`

```text
.dark {
  /* Surfaces */
  --color-bg: 10 10 11;
  --color-bg-elevated: 17 17 20;
  --color-bg-glass: 20 20 24;
```

Finally the `@theme inline` block re-exports each token as a theme color wrapped
in a color function, which is what makes the color utilities such as `bg-bg` and
`text-brand` resolve to the active token value:

Source: `apps/web/src/app/globals.css`

```text
@theme inline {
  /* Surfaces */
  --color-bg: rgb(var(--color-bg));
  --color-bg-elevated: rgb(var(--color-bg-elevated));
  --color-bg-glass: rgb(var(--color-bg-glass));
```

Reading the chain end to end: a class in the markup references a theme color, the
theme color in the `@theme inline` block points at the channel-triplet token, and
that token holds the light value under `:root` or the dark value under `.dark`.
Switching the active set is a matter of whether the `.dark` class is on the root
element, and no component's classes change.

## Common pitfalls

- **Mistake:** Storing a token as a finished color string and then trying to apply an opacity modifier to a utility that reads it.
  **Symptom:** The element renders with no color at all, because the opacity machinery expects raw channel components and cannot mix a pre-finished color string.
  **Recovery:** Store the token as its raw channel components (the three numbers) and let the theme block wrap them in the color function, so opacity modifiers can mix against the channels.

- **Mistake:** Adding a token under the document root for light mode but forgetting to add the same token name under the dark selector.
  **Symptom:** The element looks right in light mode but falls back to a wrong or default color in dark mode, because the name has no dark value to resolve to.
  **Recovery:** Declare every themeable token under both the root (light) and the dark selector, keeping the two sets in lockstep so each name resolves in either theme.

- **Mistake:** Hard-coding a raw color code directly in a component instead of referencing a token.
  **Symptom:** That element ignores theme switches and stays one fixed color in both light and dark, and it drifts from the brand when the token is updated.
  **Recovery:** Replace the raw color with the matching token-backed utility class so the element follows the active theme and updates with the token.

- **Mistake:** Expecting the theme to come from a separate configuration file the way an earlier version worked.
  **Symptom:** Edits to an external configuration file have no effect, and the styling does not change, because version 4 reads the theme from the stylesheet's theme block.
  **Recovery:** Define and edit the theme inside the stylesheet's theme block, treating the stylesheet as the single source of theme values.

## External reading

- [Tailwind CSS: theme variables](https://tailwindcss.com/docs/theme)
- [Tailwind CSS: functions and directives (including `@theme`)](https://tailwindcss.com/docs/functions-and-directives)
- [Tailwind CSS: colors and using CSS variables](https://tailwindcss.com/docs/colors)
- [MDN Web Docs: using CSS custom properties (variables)](https://developer.mozilla.org/en-US/docs/Web/CSS/Using_CSS_custom_properties)
