// ModalShell focus tests — Kanban #1315 deferred-review FOCUS-1.
//
// Strategy: render ModalShell with simple children (two buttons) and assert
// the useFocusTrap contract: initial focus lands inside the panel on open,
// Tab from the last focusable wraps to the first (and Shift+Tab the other
// way), and focus is restored to the opener button on close. ESC-close
// (already covered elsewhere in spirit) is left untouched by this change —
// not re-asserted here.
//
// Determinism (#1310): ModalShell portals to document.body, so queries go
// through document.body (mirrors AgentFormModal.test.tsx). The initial-focus
// effect defers one rAF tick, so that assertion goes through waitFor.
//
// userEvent (not fireEvent) for the OPENER click specifically: fireEvent.click
// dispatches a click event but — unlike a real browser click — does not move
// document.activeElement to the clicked element, so useFocusTrap's "capture
// the opener" step would read stale/null focus. userEvent.click simulates the
// full browser interaction (mousedown -> focus -> mouseup -> click), which is
// what the opener-capture step depends on.

import { describe, it, expect } from "vitest";
import { render, waitFor, fireEvent, configure } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";

import { ModalShell } from "@/components/ModalShell";

configure({ asyncUtilTimeout: 5000 });

// Harness: an opener button + a controlled ModalShell with two focusable
// children (mirrors a real modal's "Cancel" / "Submit" button pair).
function Harness() {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>
        Open modal
      </button>
      <ModalShell open={open} onClose={() => setOpen(false)} labelledBy="t">
        <h2 id="t">Title</h2>
        <button type="button" data-testid="modal-first">
          First
        </button>
        <button type="button" data-testid="modal-last">
          Last
        </button>
      </ModalShell>
    </div>
  );
}

describe("ModalShell — focus trap (FOCUS-1)", () => {
  it("moves focus into the panel on open", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(document.body.querySelector("button")!);

    await waitFor(() => {
      expect(document.activeElement).toHaveAttribute(
        "data-testid",
        "modal-first",
      );
    });
  });

  it("Tab from the last focusable wraps to the first", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(document.body.querySelector("button")!);

    const last = await waitFor(() => {
      const el = document.body.querySelector<HTMLElement>(
        '[data-testid="modal-last"]',
      );
      expect(el).not.toBeNull();
      return el!;
    });
    last.focus();
    expect(document.activeElement).toBe(last);

    fireEvent.keyDown(document, { key: "Tab" });
    await waitFor(() => {
      expect(document.activeElement).toHaveAttribute(
        "data-testid",
        "modal-first",
      );
    });
  });

  it("Shift+Tab from the first focusable wraps to the last", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(document.body.querySelector("button")!);

    const first = await waitFor(() => {
      const el = document.body.querySelector<HTMLElement>(
        '[data-testid="modal-first"]',
      );
      expect(el).not.toBeNull();
      return el!;
    });
    first.focus();
    expect(document.activeElement).toBe(first);

    fireEvent.keyDown(document, { key: "Tab", shiftKey: true });
    await waitFor(() => {
      expect(document.activeElement).toHaveAttribute(
        "data-testid",
        "modal-last",
      );
    });
  });

  it("restores focus to the opener button on close", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const opener = document.body.querySelector<HTMLButtonElement>("button")!;
    await user.click(opener);

    await waitFor(() => {
      expect(document.activeElement).toHaveAttribute(
        "data-testid",
        "modal-first",
      );
    });

    // ESC closes (pre-existing behavior, untouched by this change).
    fireEvent.keyDown(document, { key: "Escape" });

    await waitFor(() => {
      expect(document.activeElement).toBe(opener);
    });
  });
});
