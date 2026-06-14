// Unit tests for lib/tour.ts localStorage helpers (SSR-safe, pure).
// Placement: __tests__/ alongside other unit tests in this project.

import { describe, it, expect, beforeEach } from "vitest";
import {
  TOUR_COMPLETED_KEY,
  TOUR_PHASE_KEY,
  clearTourCompleted,
  isTourCompleted,
  markTourCompleted,
} from "@/lib/tour";

// jsdom provides localStorage — reset before each test.
beforeEach(() => {
  localStorage.clear();
});

describe("clearTourCompleted", () => {
  it("removes the completed key so isTourCompleted returns false", () => {
    localStorage.setItem(TOUR_COMPLETED_KEY, "true");
    expect(isTourCompleted()).toBe(true);

    clearTourCompleted();

    expect(localStorage.getItem(TOUR_COMPLETED_KEY)).toBeNull();
    expect(isTourCompleted()).toBe(false);
  });

  it("also clears the phase key", () => {
    localStorage.setItem(TOUR_COMPLETED_KEY, "true");
    localStorage.setItem(TOUR_PHASE_KEY, "board");

    clearTourCompleted();

    expect(localStorage.getItem(TOUR_PHASE_KEY)).toBeNull();
  });

  it("is a no-op when the key is already absent", () => {
    // Should not throw
    expect(() => clearTourCompleted()).not.toThrow();
    expect(isTourCompleted()).toBe(false);
  });

  it("markTourCompleted + clearTourCompleted round-trip", () => {
    markTourCompleted();
    expect(isTourCompleted()).toBe(true);
    clearTourCompleted();
    expect(isTourCompleted()).toBe(false);
  });
});
