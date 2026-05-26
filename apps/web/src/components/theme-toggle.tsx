"use client";

import { useSyncExternalStore } from "react";
import { Moon, Sun } from "lucide-react";
import { useTheme } from "next-themes";

import { Button } from "@/components/ui/button";

/**
 * Stable empty subscribe — `useSyncExternalStore` requires it but we never
 * actually need to react to external changes here. The two snapshot
 * functions are what give us the "post-hydration" signal.
 */
const subscribeNoop = (): (() => void) => () => {};

/**
 * Returns `false` during SSR and on the very first client render (matching
 * the server output, so hydration is clean), then flips to `true` from the
 * second render onward.
 *
 * Replaces the older `useState(false) + useEffect(() => setMounted(true), [])`
 * idiom, which the React 19 lint rule `react-hooks/set-state-in-effect` now
 * (correctly) flags as an unnecessary state-in-effect cascade.
 */
function useHasMounted(): boolean {
  return useSyncExternalStore(
    subscribeNoop,
    () => true, // client snapshot
    () => false, // server snapshot
  );
}

/**
 * Theme toggle button.
 *
 * Single-press toggle between light and dark, using `resolvedTheme` so the
 * `system` default still flips the *visible* theme (rather than only flipping
 * a stored preference value while the rendered theme stays put).
 *
 * Implementation notes:
 *
 *   - `next-themes` exposes `resolvedTheme` only after hydration; on the
 *     server and on the very first client render it is `undefined`. We gate
 *     icon rendering on a `mounted` flag so the server and the client agree
 *     on identical markup, then swap in the real icon after hydration.
 *     While `mounted` is false we render an icon-sized but visually empty
 *     button to reserve space and avoid a layout shift.
 *
 *   - The Sun and Moon icons live on top of each other and are
 *     rotated/scaled in/out based on the active theme, matching the
 *     canonical shadcn pattern. The button is `relative` so the
 *     absolutely-positioned Moon anchors inside it. Animation is brief
 *     (200ms — the design-system default for micro-interactions); CSS
 *     transitions are paused by the provider's `disableTransitionOnChange`
 *     during the theme swap itself, so the icon transition only runs on
 *     subsequent state changes.
 *
 *   - `aria-label` and the visually hidden span give screen-reader users a
 *     clear name for the control even though the visible content is icon-only.
 */
export function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme();
  const mounted = useHasMounted();

  const isDark = mounted && resolvedTheme === "dark";

  const toggle = () => {
    setTheme(isDark ? "light" : "dark");
  };

  return (
    <Button
      variant="ghost"
      size="icon"
      className="relative"
      onClick={toggle}
      aria-label="Toggle theme"
      // Until `next-themes` hydrates we don't yet know the resolved theme,
      // so disable the click to avoid setting the wrong value on a
      // pre-hydration interaction.
      disabled={!mounted}
    >
      {mounted ? (
        <>
          <Sun
            aria-hidden="true"
            className="size-4 rotate-0 scale-100 transition-all duration-200 dark:-rotate-90 dark:scale-0"
          />
          <Moon
            aria-hidden="true"
            className="absolute size-4 rotate-90 scale-0 transition-all duration-200 dark:rotate-0 dark:scale-100"
          />
        </>
      ) : (
        // Placeholder keeps the button the same size pre-hydration.
        <span aria-hidden="true" className="size-4" />
      )}
      <span className="sr-only">Toggle theme</span>
    </Button>
  );
}
