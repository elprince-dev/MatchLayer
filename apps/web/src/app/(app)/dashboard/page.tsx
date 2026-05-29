"use client";

import * as React from "react";

import { useAuth } from "@/lib/auth";

/**
 * Placeholder dashboard — the landing page for authenticated users.
 */
export default function DashboardPage(): React.JSX.Element {
  const { user } = useAuth();

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-text">
        Welcome{user ? `, ${user.display_name}` : ""}.
      </h1>
      <p className="text-text-muted">
        This is the MatchLayer dashboard. Resume upload and scoring are coming
        in the next phase.
      </p>
    </div>
  );
}
