"use client";

import * as React from "react";

import { BulletRewritePanel } from "@/components/llm/BulletRewritePanel";
import { CoachPanel } from "@/components/llm/CoachPanel";
import { InterviewPanel } from "@/components/llm/InterviewPanel";
import { cn } from "@/lib/utils";

/**
 * LlmTabs — the three LLM features as tabs anchored to the displayed
 * Match_Result (phase-3-llm-layer Task 11.5; Req 17.1).
 *
 * Rendered inside the `(app)/matches/[id]` results page, so it inherits
 * the route group's `robots: { index: false, follow: false }` and the
 * `X-Robots-Tag` response header (`seo.md`) — no metadata is added here.
 *
 * ## Panels stay mounted
 * All three panels mount once and inactive ones are hidden with the
 * `hidden` attribute rather than unmounted. This keeps an in-flight SSE
 * stream (and its resolved state) alive across tab switches, and it
 * means each panel's on-load persisted-result GET (Req 17.8) runs once
 * per page view — never re-fired by tab navigation.
 *
 * ## Accessibility (WAI-ARIA tabs pattern)
 * `role="tablist"` / `role="tab"` / `role="tabpanel"` with
 * `aria-selected`, `aria-controls` / `aria-labelledby` wiring, a roving
 * tabindex, and Left/Right/Home/End keyboard activation. Focus rings
 * are the shared branded treatment (design.md: never `outline: none`
 * without a replacement).
 *
 * ## Calm app-shell styling (design.md "Where to be fancy vs calm")
 * An underline tab bar on the standard tokens — no animation noise; the
 * streaming panels provide all the motion this section needs.
 */
export interface LlmTabsProps {
  /** The viewed Match_Result id the features are anchored to. */
  matchId: string;
  /** Composition hook. */
  className?: string;
}

type TabId = "coach" | "bullets" | "interview";

const TABS: readonly { id: TabId; label: string }[] = [
  { id: "coach", label: "Coach" },
  { id: "bullets", label: "Bullet Rewrites" },
  { id: "interview", label: "Interview Prep" },
] as const;

export function LlmTabs({
  matchId,
  className,
}: LlmTabsProps): React.JSX.Element {
  const [activeTab, setActiveTab] = React.useState<TabId>("coach");
  const tabRefs = React.useRef<
    Partial<Record<TabId, HTMLButtonElement | null>>
  >({});
  const idBase = React.useId();

  const focusAndActivate = (tabId: TabId): void => {
    setActiveTab(tabId);
    tabRefs.current[tabId]?.focus();
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    const currentIndex = TABS.findIndex((tab) => tab.id === activeTab);
    let nextIndex: number | null = null;

    switch (event.key) {
      case "ArrowRight":
        nextIndex = (currentIndex + 1) % TABS.length;
        break;
      case "ArrowLeft":
        nextIndex = (currentIndex - 1 + TABS.length) % TABS.length;
        break;
      case "Home":
        nextIndex = 0;
        break;
      case "End":
        nextIndex = TABS.length - 1;
        break;
      default:
        return;
    }

    event.preventDefault();
    const next = TABS[nextIndex];
    if (next !== undefined) {
      focusAndActivate(next.id);
    }
  };

  return (
    <section className={cn("space-y-6", className)}>
      <h3 className="text-lg font-semibold tracking-tight text-text">
        AI tools
      </h3>

      <div
        role="tablist"
        aria-label="AI tools"
        onKeyDown={handleKeyDown}
        className="flex gap-1 border-b border-border"
      >
        {TABS.map((tab) => {
          const selected = tab.id === activeTab;
          return (
            <button
              key={tab.id}
              ref={(node) => {
                tabRefs.current[tab.id] = node;
              }}
              type="button"
              role="tab"
              id={`${idBase}-tab-${tab.id}`}
              aria-selected={selected}
              aria-controls={`${idBase}-panel-${tab.id}`}
              tabIndex={selected ? 0 : -1}
              onClick={() => setActiveTab(tab.id)}
              className={cn(
                "-mb-px rounded-t-md border-b-2 px-4 py-2 text-sm font-medium transition-colors",
                "focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg focus-visible:outline-none",
                selected
                  ? "border-brand text-text"
                  : "border-transparent text-text-muted hover:text-text",
              )}
            >
              {tab.label}
            </button>
          );
        })}
      </div>

      {TABS.map((tab) => (
        <div
          key={tab.id}
          role="tabpanel"
          id={`${idBase}-panel-${tab.id}`}
          aria-labelledby={`${idBase}-tab-${tab.id}`}
          hidden={tab.id !== activeTab}
          tabIndex={0}
          className="focus-visible:ring-2 focus-visible:ring-brand focus-visible:outline-none"
        >
          {tab.id === "coach" && <CoachPanel matchId={matchId} />}
          {tab.id === "bullets" && <BulletRewritePanel matchId={matchId} />}
          {tab.id === "interview" && <InterviewPanel matchId={matchId} />}
        </div>
      ))}
    </section>
  );
}
