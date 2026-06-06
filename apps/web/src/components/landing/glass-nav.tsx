"use client";

import { useState, useSyncExternalStore } from "react";
import Link from "next/link";
import { Menu, X } from "lucide-react";

import { ThemeToggle } from "@/components/theme-toggle";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/**
 * Glass_Nav — the Landing_Page top navigation (design Section 7.3 GlassNav,
 * Section 8.2; Req 6.1–6.6).
 *
 * `'use client'` because it owns two pieces of browser state: the scroll
 * position (drives the transparent → glass background swap) and the mobile
 * menu open/closed flag.
 *
 * ## Layout
 *
 *   - Fixed to the top of the viewport, full width, above page content (Req 6.1).
 *   - Left: the "MatchLayer" wordmark in the violet→cyan brand gradient — the
 *     same `bg-gradient-to-br from-brand to-brand-2 bg-clip-text text-transparent`
 *     recipe used by the auth layout and app shell. `bg-clip-text` only paints
 *     the glyphs; the literal text stays in the accessibility tree.
 *   - Desktop (≥768px): in-page anchor links (Features, How It Works, About),
 *     a "Sign in" ghost button → `/login`, a "Get started" primary button →
 *     `/register`, and the existing {@link ThemeToggle}.
 *   - Mobile (<768px): the links + CTAs collapse behind a hamburger toggle that
 *     reports its state via `aria-expanded` / `aria-controls` (Req 6.4); the
 *     ThemeToggle stays visible alongside it.
 *
 * There is intentionally **no "Pricing" link** and no link to any capability
 * that does not exist in the Phase 1 MVP (Req 6.2) — the only outbound targets
 * are the in-page section anchors and the `/login` / `/register` auth surface.
 *
 * ## Scroll behavior (Req 6.5, 6.6)
 *
 * Over the hero the bar is fully transparent. Once the viewport top scrolls
 * past the bottom edge of the hero it transitions to the `bg-glass` frosted
 * surface (12px backdrop-blur; 65% opacity light / 55% dark) — and back to
 * transparent when scrolled above the hero again. The swap runs on the
 * `--motion-micro` (200ms) timing token, so it also respects
 * `prefers-reduced-motion` (the token collapses to 0ms) while still being the
 * functional nav-feedback the design calls for.
 *
 * The scrolled flag is derived through `useSyncExternalStore` rather than a
 * `useState` + scroll-effect pair: the store subscribes to `scroll`/`resize`
 * with a passive, rAF-coalesced listener and the snapshot reads the live DOM,
 * which keeps the read off React's render-commit path and sidesteps the
 * `react-hooks/set-state-in-effect` lint rule. The server snapshot is
 * `false` (transparent), matching the at-top first paint so hydration is clean.
 */

/** Id linking the hamburger's `aria-controls` to the collapsible mobile menu. */
const MOBILE_MENU_ID = "glass-nav-mobile-menu";

/** In-page section anchors. No "Pricing"/non-MVP targets (Req 6.2). */
const NAV_LINKS: ReadonlyArray<{ href: string; label: string }> = [
  { href: "#features", label: "Features" },
  { href: "#how-it-works", label: "How It Works" },
  { href: "#about", label: "About" },
];

/**
 * Whether the viewport has scrolled past the bottom edge of the hero.
 *
 * The hero is looked up by its `#hero` id (rendered by the Hero component /
 * page assembly); when it is absent — e.g. during isolated component tests —
 * we fall back to one viewport height, a sane approximation of the ~70vh hero.
 */
function computeScrolled(): boolean {
  if (typeof window === "undefined") {
    return false;
  }
  const hero = document.getElementById("hero");
  const heroBottom = hero
    ? hero.offsetTop + hero.offsetHeight
    : window.innerHeight;
  return window.scrollY >= heroBottom;
}

/**
 * Subscribe to the scroll/resize signals that can change the scrolled state.
 * Updates are coalesced into a single `requestAnimationFrame` callback so a
 * burst of scroll events triggers at most one store notification per frame.
 */
function subscribeScrolled(onStoreChange: () => void): () => void {
  let frame = 0;
  const handle = (): void => {
    if (frame !== 0) {
      return;
    }
    frame = window.requestAnimationFrame(() => {
      frame = 0;
      onStoreChange();
    });
  };
  window.addEventListener("scroll", handle, { passive: true });
  window.addEventListener("resize", handle, { passive: true });
  return () => {
    if (frame !== 0) {
      window.cancelAnimationFrame(frame);
    }
    window.removeEventListener("scroll", handle);
    window.removeEventListener("resize", handle);
  };
}

export interface GlassNavProps {
  className?: string;
}

export function GlassNav({ className }: GlassNavProps): React.JSX.Element {
  const [menuOpen, setMenuOpen] = useState(false);

  const scrolled = useSyncExternalStore(
    subscribeScrolled,
    computeScrolled,
    () => false,
  );

  const closeMenu = (): void => {
    setMenuOpen(false);
  };

  // Shared focus-ring treatment for the bare anchor links + brand mark, so they
  // match the 2px branded ring the Button primitive already applies.
  const focusRing =
    "rounded-md outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg";

  // Glass surface utilities, applied to the bar when scrolled and always to the
  // open mobile panel so its contents stay legible over the hero.
  const glassSurface =
    "bg-bg-glass/65 backdrop-blur-md dark:bg-bg-glass/55 border-b border-border";

  return (
    <header
      className={cn(
        "fixed inset-x-0 top-0 z-50 transition",
        scrolled ? glassSurface : "border-b border-transparent bg-transparent",
        className,
      )}
      style={{
        transitionDuration: "var(--motion-micro)",
        transitionTimingFunction: "var(--motion-ease)",
      }}
    >
      <div className="mx-auto flex h-16 max-w-7xl items-center justify-between px-6">
        {/* Brand mark → top of the landing page. */}
        <Link
          href="/"
          className={cn(
            "bg-gradient-to-br from-brand to-brand-2 bg-clip-text font-sans text-xl font-semibold tracking-tight text-transparent",
            focusRing,
          )}
        >
          MatchLayer
        </Link>

        {/* Desktop cluster (≥768px): in-page nav + CTAs + theme toggle. */}
        <div className="hidden items-center gap-2 md:flex">
          <nav aria-label="Primary" className="flex items-center gap-1">
            {NAV_LINKS.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                className={cn(
                  "px-3 py-2 text-sm font-medium text-text-muted transition-colors hover:text-text",
                  focusRing,
                )}
              >
                {link.label}
              </Link>
            ))}
          </nav>
          <Button asChild variant="ghost" size="sm">
            <Link href="/login">Sign in</Link>
          </Button>
          <Button asChild size="sm">
            <Link href="/register">Get started</Link>
          </Button>
          <ThemeToggle />
        </div>

        {/* Mobile cluster (<768px): theme toggle + hamburger. */}
        <div className="flex items-center gap-1 md:hidden">
          <ThemeToggle />
          <Button
            variant="ghost"
            size="icon"
            aria-label={menuOpen ? "Close menu" : "Open menu"}
            aria-expanded={menuOpen}
            aria-controls={MOBILE_MENU_ID}
            onClick={() => setMenuOpen((open) => !open)}
          >
            {menuOpen ? (
              <X aria-hidden="true" className="size-5" />
            ) : (
              <Menu aria-hidden="true" className="size-5" />
            )}
          </Button>
        </div>
      </div>

      {/* Collapsible mobile menu. Kept in the DOM (toggled via `hidden`) so the
       * hamburger's `aria-controls` always resolves; `md:hidden` guarantees it
       * never shows on desktop regardless of `menuOpen`. */}
      <nav
        id={MOBILE_MENU_ID}
        aria-label="Mobile"
        className={cn("md:hidden", glassSurface, menuOpen ? "block" : "hidden")}
      >
        <div className="flex flex-col gap-1 px-6 pb-4 pt-2">
          {NAV_LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              onClick={closeMenu}
              className={cn(
                "px-3 py-2 text-sm font-medium text-text-muted transition-colors hover:text-text",
                focusRing,
              )}
            >
              {link.label}
            </Link>
          ))}
          <div className="mt-2 flex flex-col gap-2">
            <Button asChild variant="ghost" className="w-full justify-center">
              <Link href="/login" onClick={closeMenu}>
                Sign in
              </Link>
            </Button>
            <Button asChild className="w-full justify-center">
              <Link href="/register" onClick={closeMenu}>
                Get started
              </Link>
            </Button>
          </div>
        </div>
      </nav>
    </header>
  );
}
