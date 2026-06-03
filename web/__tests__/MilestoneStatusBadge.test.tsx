// Smoke + unit tests for MilestoneStatusBadge — pure presentational component.
// No mocks needed (no hooks, no navigation, no API calls).

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { MilestoneStatusBadge } from "@/components/MilestoneStatusBadge";
import type { MilestoneStatusValue } from "@/lib/api";

const STATUSES: MilestoneStatusValue[] = ["planned", "active", "released", "cancelled"];

describe("MilestoneStatusBadge", () => {
  it.each(STATUSES)("renders without crashing for status=%s", (status) => {
    const { container } = render(<MilestoneStatusBadge status={status} />);
    expect(container.firstChild).not.toBeNull();
  });

  it("renders the label text for each status", () => {
    for (const status of STATUSES) {
      const { unmount } = render(<MilestoneStatusBadge status={status} />);
      expect(screen.getByText(status)).toBeInTheDocument();
      unmount();
    }
  });

  it("sets data-milestone-status attribute to the status value", () => {
    const { container } = render(<MilestoneStatusBadge status="active" />);
    const badge = container.querySelector("[data-milestone-status]");
    expect(badge).not.toBeNull();
    expect(badge?.getAttribute("data-milestone-status")).toBe("active");
  });

  it("planned badge has zinc styling class", () => {
    const { container } = render(<MilestoneStatusBadge status="planned" />);
    const badge = container.firstChild as HTMLElement;
    expect(badge.className).toContain("bg-zinc-100");
  });

  it("active badge has amber styling class", () => {
    const { container } = render(<MilestoneStatusBadge status="active" />);
    const badge = container.firstChild as HTMLElement;
    expect(badge.className).toContain("bg-amber-50");
  });

  it("released badge has emerald styling class", () => {
    const { container } = render(<MilestoneStatusBadge status="released" />);
    const badge = container.firstChild as HTMLElement;
    expect(badge.className).toContain("bg-emerald-50");
  });

  it("cancelled badge has red styling class", () => {
    const { container } = render(<MilestoneStatusBadge status="cancelled" />);
    const badge = container.firstChild as HTMLElement;
    expect(badge.className).toContain("bg-red-50");
  });
});
