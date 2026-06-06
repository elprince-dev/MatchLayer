/**
 * Unit tests for `FeatureCard` (Task 8.8).
 *
 * Validates the Landing_Page feature tile against its acceptance criteria
 * (Req 5.2, 5.5; design Section 7.3 "FeatureCard"):
 *
 *   - Req 5.5 — when a card marks a Roadmap_Feature via `badge`
 *     ("Coming soon" / "Planned"), it must render **no enabled control implying
 *     the feature is usable now**: the badge is a non-interactive `<span>`
 *     pill (never a button/link/role), and the whole card is a plain `<div>`,
 *     not an anchor or button.
 *   - Req 5.2 — the roadmap label is rendered adjacent to the feature so it is
 *     visually distinguished from a current capability.
 *
 * `FeatureCard` is a Server Component with no client features (no state,
 * effects, or handlers — the hover affordance is pure CSS), so it renders
 * directly under jsdom with no provider, router, or framer-motion stubbing.
 * Conventions mirror the co-located results tests: render/screen/cleanup,
 * `toBeInstanceOf`, no jest-dom matchers.
 *
 * @vitest-environment jsdom
 */

import * as React from "react";

import { ShieldCheck } from "lucide-react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { FeatureCard } from "@/components/landing/feature-card";

afterEach(() => {
  cleanup();
});

describe("FeatureCard — renders content", () => {
  it("renders the title and description", () => {
    render(
      <FeatureCard
        icon={ShieldCheck}
        title="Privacy-first processing"
        description="Your resume text is analyzed and never shared."
      />,
    );

    expect(screen.getByText("Privacy-first processing")).toBeInstanceOf(
      HTMLElement,
    );
    expect(
      screen.getByText("Your resume text is analyzed and never shared."),
    ).toBeInstanceOf(HTMLElement);
  });

  it("renders the title as an <h3> (sequential heading level under the section <h2>)", () => {
    const { container } = render(
      <FeatureCard
        icon={ShieldCheck}
        title="Transparent ATS score"
        description="See how an ATS reads your resume."
      />,
    );

    const heading = container.querySelector("h3");
    expect(heading).toBeInstanceOf(HTMLHeadingElement);
    expect(heading?.textContent).toBe("Transparent ATS score");
  });
});

describe("FeatureCard — roadmap badge renders no usable control (Req 5.5)", () => {
  it("renders the badge text adjacent to the feature (Req 5.2)", () => {
    render(
      <FeatureCard
        icon={ShieldCheck}
        title="Semantic analysis"
        description="Deeper meaning-based matching is on the roadmap."
        badge="Coming soon"
      />,
    );

    expect(screen.getByText("Coming soon")).toBeInstanceOf(HTMLElement);
  });

  it("renders the badge as a non-interactive <span>, never a control", () => {
    const { container } = render(
      <FeatureCard
        icon={ShieldCheck}
        title="Semantic analysis"
        description="Deeper meaning-based matching is on the roadmap."
        badge="Planned"
      />,
    );

    const badge = container.querySelector('[data-slot="feature-card-badge"]');
    expect(badge).toBeInstanceOf(HTMLSpanElement);
    // No interactive role/affordance: not a button, link, toggle, or anything
    // suggesting the planned capability is usable now (Req 5.5).
    expect(badge?.getAttribute("role")).toBeNull();
    expect(badge?.hasAttribute("href")).toBe(false);
    expect(badge?.hasAttribute("onclick")).toBe(false);
  });

  it("renders no button, link, or interactive role anywhere in the card", () => {
    render(
      <FeatureCard
        icon={ShieldCheck}
        title="Semantic analysis"
        description="Deeper meaning-based matching is on the roadmap."
        badge="Coming soon"
      />,
    );

    // The whole card is presentational — nothing to activate (Req 5.5).
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("renders the whole card as a plain <div>, not an anchor or button", () => {
    const { container } = render(
      <FeatureCard
        icon={ShieldCheck}
        title="Semantic analysis"
        description="Deeper meaning-based matching is on the roadmap."
        badge="Coming soon"
      />,
    );

    const card = container.querySelector('[data-slot="feature-card"]');
    expect(card).toBeInstanceOf(HTMLDivElement);
  });
});

describe("FeatureCard — current capability (no badge)", () => {
  it("renders no roadmap label when `badge` is omitted", () => {
    const { container } = render(
      <FeatureCard
        icon={ShieldCheck}
        title="Score breakdown"
        description="See how similarity and coverage combine."
      />,
    );

    expect(
      container.querySelector('[data-slot="feature-card-badge"]'),
    ).toBeNull();
    expect(screen.queryByText("Coming soon")).toBeNull();
    expect(screen.queryByText("Planned")).toBeNull();
  });
});
