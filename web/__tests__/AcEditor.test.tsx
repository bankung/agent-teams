/**
 * Tests for AcEditor mobile long-press quick-edit (#1013)
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, act, waitFor } from "@testing-library/react";
import { AcEditor } from "@/components/AcEditor";
import type { AcceptanceCriterion } from "@/lib/api";

const CRITERIA: AcceptanceCriterion[] = [
  { text: "First criterion", status: "pending", verified_by: null, verified_at: null, notes: null },
  { text: "Second criterion", status: "passed", verified_by: "operator", verified_at: "2026-01-01T00:00:00Z", notes: null },
];

function makeSave() {
  return vi.fn(async () => {});
}

/** Simulate a long-press: touchStart → advance fake timers → touchEnd */
function longPress(el: Element, ms = 500) {
  act(() => {
    fireEvent.touchStart(el);
    vi.advanceTimersByTime(ms);
    fireEvent.touchEnd(el);
  });
}

describe("AcEditor quick-edit (long-press)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("(a) long-press shows inline textarea with line text", () => {
    const onSave = makeSave();
    render(<AcEditor criteria={CRITERIA} isTerminal={false} onSave={onSave} />);

    const items = screen.getAllByRole("listitem");
    longPress(items[0]);

    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(textarea).toHaveAttribute("data-ac-quickedit", "0");
    expect(textarea.value).toBe("First criterion");
  });

  it("(b) change text + blur → onSave called with updated array", async () => {
    const onSave = makeSave();
    render(<AcEditor criteria={CRITERIA} isTerminal={false} onSave={onSave} />);

    const items = screen.getAllByRole("listitem");
    longPress(items[0]);

    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "Updated text" } });

    await act(async () => {
      fireEvent.blur(textarea);
    });

    expect(onSave).toHaveBeenCalledOnce();
    const arg = onSave.mock.calls[0][0] as AcceptanceCriterion[];
    expect(arg[0].text).toBe("Updated text");
    expect(arg[1].text).toBe("Second criterion");
    expect(arg.length).toBe(CRITERIA.length);
  });

  it("(c) blur with unchanged text → onSave NOT called", async () => {
    const onSave = makeSave();
    render(<AcEditor criteria={CRITERIA} isTerminal={false} onSave={onSave} />);

    const items = screen.getAllByRole("listitem");
    longPress(items[1]);

    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    // text unchanged — blur immediately
    await act(async () => {
      fireEvent.blur(textarea);
    });

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("(d) terminal → no quick-edit on long-press", () => {
    const onSave = makeSave();
    render(<AcEditor criteria={CRITERIA} isTerminal={true} onSave={onSave} />);

    const items = screen.getAllByRole("listitem");
    act(() => {
      fireEvent.touchStart(items[0]);
      vi.advanceTimersByTime(600);
      fireEvent.touchEnd(items[0]);
    });

    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("(d) disabled → no quick-edit on long-press", () => {
    const onSave = makeSave();
    render(<AcEditor criteria={CRITERIA} isTerminal={false} onSave={onSave} disabled={true} />);

    const items = screen.getAllByRole("listitem");
    act(() => {
      fireEvent.touchStart(items[0]);
      vi.advanceTimersByTime(600);
      fireEvent.touchEnd(items[0]);
    });

    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("short tap (< 500ms) does NOT trigger quick-edit", () => {
    const onSave = makeSave();
    render(<AcEditor criteria={CRITERIA} isTerminal={false} onSave={onSave} />);

    const items = screen.getAllByRole("listitem");
    act(() => {
      fireEvent.touchStart(items[0]);
      vi.advanceTimersByTime(200);
      fireEvent.touchEnd(items[0]); // clears timer before 500ms
      vi.advanceTimersByTime(400); // total 600ms but timer already cleared
    });

    expect(screen.queryByRole("textbox")).toBeNull();
  });

  it("onToast called with 'AC updated' on successful save when prop provided", async () => {
    const onSave = makeSave();
    const onToast = vi.fn();
    render(
      <AcEditor criteria={CRITERIA} isTerminal={false} onSave={onSave} onToast={onToast} />
    );

    const items = screen.getAllByRole("listitem");
    longPress(items[0]);

    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "Changed text" } });

    await act(async () => {
      fireEvent.blur(textarea);
    });

    expect(onToast).toHaveBeenCalledWith("AC updated");
  });

  it("Escape key exits quick-edit without saving", () => {
    const onSave = makeSave();
    render(<AcEditor criteria={CRITERIA} isTerminal={false} onSave={onSave} />);

    const items = screen.getAllByRole("listitem");
    longPress(items[0]);

    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "Some change" } });

    act(() => {
      fireEvent.keyDown(textarea, { key: "Escape" });
    });

    expect(screen.queryByRole("textbox")).toBeNull();
    expect(onSave).not.toHaveBeenCalled();
  });
});

// Kanban #2841 — edit-panel Save must not become an unhandled promise
// rejection when onSave (TaskDetail.handleAcSave) rejects. Listens on
// process "unhandledRejection" directly rather than inferring it from DOM
// state, since the DOM-visible effects (edit view staying open) already
// happened before the rejection propagates — asserting on them alone would
// pass even without the fix (Node's unhandledRejection is the only signal
// that actually discriminates the try/finally-with-no-catch bug).
describe("AcEditor — edit-panel Save error handling", () => {
  it("Save rejecting does not produce an unhandled promise rejection", async () => {
    const onSave = vi.fn().mockRejectedValue(new Error("boom"));
    const unhandled: unknown[] = [];
    const onUnhandledRejection = (reason: unknown) => unhandled.push(reason);
    process.on("unhandledRejection", onUnhandledRejection);

    try {
      render(<AcEditor criteria={CRITERIA} isTerminal={false} onSave={onSave} />);
      fireEvent.click(screen.getByRole("button", { name: /edit acceptance criteria/i }));
      fireEvent.click(document.querySelector("[data-ac-save]")!);

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      // Let any pending unhandledRejection callback fire before asserting.
      await new Promise((resolve) => setTimeout(resolve, 0));
    } finally {
      process.off("unhandledRejection", onUnhandledRejection);
    }

    expect(unhandled).toHaveLength(0);
    // setEditing(false) was skipped — the Save button (edit view) is still present.
    expect(document.querySelector("[data-ac-save]")).not.toBeNull();
  });
});

// Kanban #2841 — parity with NewTaskModal's create-time >50 guard + the
// backend hard cap (api/src/schemas/task.py max_length=50).
describe("AcEditor — addItem 50-item cap", () => {
  it("blocks adding a 51st item and surfaces onToast", () => {
    const onSave = makeSave();
    const onToast = vi.fn();
    const fifty: AcceptanceCriterion[] = Array.from({ length: 50 }, (_, i) => ({
      text: `criterion ${i}`,
      status: "pending",
      verified_by: null,
      verified_at: null,
      notes: null,
    }));
    render(
      <AcEditor criteria={fifty} isTerminal={false} onSave={onSave} onToast={onToast} />,
    );

    fireEvent.click(screen.getByRole("button", { name: /edit acceptance criteria/i }));
    expect(screen.getAllByRole("textbox")).toHaveLength(50);

    fireEvent.click(screen.getByRole("button", { name: /add acceptance criterion/i }));

    // Still 50 — the 51st was blocked.
    expect(screen.getAllByRole("textbox")).toHaveLength(50);
    expect(onToast).toHaveBeenCalledWith(expect.stringContaining("50"));
  });
});
