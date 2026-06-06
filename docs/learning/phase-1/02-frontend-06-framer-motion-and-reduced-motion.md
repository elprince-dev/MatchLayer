# Framer Motion and the reduced-motion accessibility pattern

## Introduction

This document explains how an interface plays smooth, purposeful animations
while still respecting a reader who has asked their device to keep motion to a
minimum. The animation tool is Framer Motion (an animation library for React,
the JavaScript library for building user interfaces — a library is a reusable
package of code you import rather than write yourself). The accessibility
concern is the operating-system setting some people enable, called
"reduced motion", which signals that large movement on screen makes them unwell
or distracted. The pattern this document teaches is a single place in the code
that reads that setting and neutralises animation when it is on, so individual
animated elements never have to check it themselves.

**Learning outcomes** — after reading this document you will be able to:

- Explain what a declarative animation library does and why describing an animation as data is easier to reason about than hand-writing each frame. You state the start and end and the library fills in the motion between them.
- Describe the reduced-motion operating-system preference and why an interface must honour it. Honouring it is an accessibility obligation, not a nicety.
- Read code that reads the reduced-motion preference through a hook and disables motion in one shared wrapper. A hook is a function a component calls to subscribe to a piece of changing state.
- Recognise the common mistakes around motion accessibility and recover from them. Most defects come from animating around the shared wrapper instead of through it.

Prerequisites: this document builds on
[The Next.js App Router and React Server Components](02-frontend-01-nextjs-app-router-and-rsc.md),
because the animation hook described here only runs in a Client Component (a
component that ships to and executes in the browser, as opposed to one rendered
only on the server), and that distinction is introduced there.

## Problem it solves

Motion on a screen is not free of cost for every reader. People with vestibular
disorders (inner-ear conditions affecting balance) can feel dizzy or nauseated
when large elements slide, zoom, or parallax across the page. Operating systems
expose a preference for these readers — "reduce motion" — and a considerate
interface must turn its animations off, or down, when that preference is set.
The concrete problem is: how do you keep tasteful animation for most readers
while removing it for the readers who asked for less, without scattering the
same conditional check through every animated component?

The common prior approach was to animate unconditionally — every entrance fade,
every count-up, every layout slide ran for everybody, with no regard for the
preference. The next, slightly better approach repeated a preference check
inside each animated component, so one component might honour the setting while
the one next to it forgot, and the behaviour drifted as the interface grew.

A declarative animation library combined with a single shared decision point
solves both problems. The library makes each animation a small description that
is easy to override, and the shared decision point reads the preference once so
every animated element inherits the same correct behaviour.

## Mental model

Think of the reduced-motion preference as a master dimmer switch for movement,
and the shared wrapper as the one switch every animated light in the building is
wired through. Flip the dimmer and every light dims together; no individual lamp
needs its own switch, and none can be left burning by accident.

When an animated element renders through that wrapper, the sequence is:

1. The element describes its animation as data: where it starts (for example, faded out and shifted down) and where it ends (fully visible, in place).
2. The shared wrapper asks the system, through a hook, whether the reader prefers reduced motion.
3. If there is no such preference, the wrapper passes the description through untouched and the animation plays normally.
4. If the reader prefers reduced motion, the wrapper rewrites the description so the element's animated state equals its starting state and the duration is zero, so it appears in its final position instantly with no movement.
5. Because every animated element is wired through the same wrapper, this decision is made identically everywhere, and no component re-implements the check.

Steps 2 and 4 are the heart of the pattern: read the preference in one place,
and when it is set, collapse the motion to nothing.

## How it works

A declarative animation library lets a component describe an animation instead
of drawing each frame by hand. You give an element an initial state and a target
state — a set of style values such as opacity and position — and the library
animates the difference over a duration you choose. Because the animation is
plain data, another piece of code can inspect or replace it before it runs.

Operating systems let a person request less on-screen movement through a setting
usually labelled "reduce motion". Browsers expose that setting to a page in two
forms. The styling layer can detect it with a media query (a conditional rule
that applies styles only when an environment condition holds) named
`prefers-reduced-motion`. Code can detect the same setting through a small
function the animation library provides — a hook, which is a function a
component calls to read a value that can change while the page is open. The hook
returns whether the reader prefers reduced motion, and re-runs the component if
that ever changes.

The accessibility pattern places one wrapper component between the animation
library and the rest of the interface. Every element that wants to animate
renders through this wrapper and hands it the animation description. The wrapper
calls the preference hook. When no preference is set, it forwards the
description unchanged. When the reader prefers reduced motion, the wrapper
overrides two fields of the description: it sets the animated target equal to
the initial state, so there is nothing to move toward, and it sets the duration
to zero, so any remaining transition resolves instantly. The element therefore
renders in its final, static appearance with no perceptible motion.

Centralising the decision has a maintenance payoff. A new animated element
inherits correct behaviour for free by rendering through the wrapper, and a
reviewer has exactly one place to audit. The pattern usually carves out a
deliberate exception for feedback indicators — a loading shimmer or a progress
bar — because a small, looping indicator communicates that work is happening and
is not the kind of large decorative movement the preference is meant to
suppress.

## MatchLayer Phase 1 usage

In MatchLayer the shared wrapper lives in
`apps/web/src/components/motion-safe.tsx`. It imports the animation primitives
and, critically, the preference hook from the animation library:

Source: `apps/web/src/components/motion-safe.tsx`

```tsx
import { motion, useReducedMotion, type MotionProps } from "framer-motion";
```

The decision is made once, inside a small hook that takes an element's motion
description and returns it unchanged when no preference is set, or a
motion-free version when the reader prefers reduced motion:

Source: `apps/web/src/components/motion-safe.tsx`

```tsx
export function useMotionSafeProps<T extends MotionProps>(props: T): T {
  const reduced = useReducedMotion();

  if (!reduced) {
    return props;
  }

  // The cast preserves the caller-supplied generic shape (e.g.
  // `HTMLMotionProps<"h1">`). We only override two fields that are part of
  // every `MotionProps` shape, so widening to the base type and back is safe.
  return {
    ...props,
    animate: props.initial,
    transition: { duration: 0 },
  } as T;
}
```

Reading it line by line: `useReducedMotion()` returns the reader's preference;
when it is false (or not yet known) the description passes straight through;
when it is true the returned description forces the animated target (`animate`)
to equal the starting state (`props.initial`) and the `transition` duration to
`0`. Every entrance and reveal animation in the web app funnels through this one
hook, so the reduced-motion rule is enforced uniformly rather than re-checked in
each component. Looping feedback indicators such as the upload progress bar are
intentionally implemented outside this wrapper, because they must keep animating
to signal ongoing work.

## Common pitfalls

- **Mistake:** Animating an element directly with the library instead of routing its motion description through the shared reduced-motion wrapper.
  **Symptom:** Most of the interface stops moving when the reader enables reduce-motion, but this one element still slides or fades, so the behaviour is inconsistent and the preference is partly ignored.
  **Recovery:** Render the element through the shared wrapper (or its hook) so the single reduced-motion decision applies to it like everything else.

- **Mistake:** When reduced motion is on, leaving the element's starting state hidden (for example, fully transparent) instead of setting the animated state to the visible final state.
  **Symptom:** The element never appears at all for reduced-motion readers, because with the duration set to zero it is frozen in its hidden starting state.
  **Recovery:** Make the reduced-motion path resolve the element to its final, visible appearance immediately, so removing the motion does not also remove the content.

- **Mistake:** Suppressing every animation under reduced motion, including small looping feedback indicators like a loading spinner or progress bar.
  **Symptom:** A reader who enabled reduce-motion gets no visible signal that an upload or analysis is in progress, so the interface looks frozen or broken.
  **Recovery:** Exempt feedback indicators from the wrapper; reduce-motion targets large decorative movement, not the small ongoing-work cues a reader needs.

- **Mistake:** Calling the preference hook from a component that only ever runs on the server.
  **Symptom:** The build or render fails because the hook depends on browser state that does not exist during server rendering.
  **Recovery:** Mark the animated component as a Client Component so the hook runs in the browser where the preference can actually be read.

## External reading

- [MDN Web Docs: prefers-reduced-motion media feature](https://developer.mozilla.org/en-US/docs/Web/CSS/@media/prefers-reduced-motion)
- [W3C: WCAG Success Criterion 2.3.3 Animation from Interactions](https://www.w3.org/WAI/WCAG21/Understanding/animation-from-interactions.html)
- [Framer Motion: useReducedMotion hook](https://motion.dev/docs/react-use-reduced-motion)
