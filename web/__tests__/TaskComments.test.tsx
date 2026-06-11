// Component tests for TaskComments — Kanban #1005 (AC#5 thread + AC#6 render).
//
// Strategy: mock @/lib/api (getTaskComments + postTaskComment), render the
// component, and assert: (1) existing comments render, (2) a markdown body with
// an injected <script> / <img onerror> does NOT execute / is stripped, (3) the
// compose box posts as author_kind="user" and the new row appears.
//
// Determinism: all assertions use findBy*/waitFor (async-fetch RTL races hide
// behind sync querySelector). asyncUtilTimeout raised for full-suite CPU load.

import { describe, it, expect, vi, beforeEach } from "vitest";
import {
  render,
  screen,
  waitFor,
  fireEvent,
  configure,
} from "@testing-library/react";
import type { TaskCommentRead } from "@/lib/api";

configure({ asyncUtilTimeout: 5000 });

const mockGetTaskComments = vi.fn();
const mockPostTaskComment = vi.fn();

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    getTaskComments: (...args: Parameters<typeof actual.getTaskComments>) =>
      mockGetTaskComments(...args),
    postTaskComment: (...args: Parameters<typeof actual.postTaskComment>) =>
      mockPostTaskComment(...args),
  };
});

// Imported AFTER the mock is registered.
import { TaskComments } from "@/components/TaskComments";

function comment(over: Partial<TaskCommentRead> = {}): TaskCommentRead {
  return {
    id: 1,
    task_id: 1005,
    author_kind: "user",
    author_label: "operator",
    body: "hello world",
    body_markdown: true,
    created_at: "2026-06-08T10:00:00Z",
    ...over,
  };
}

beforeEach(() => {
  mockGetTaskComments.mockReset();
  mockPostTaskComment.mockReset();
});

describe("TaskComments — thread rendering", () => {
  it("renders existing comments oldest-first", async () => {
    mockGetTaskComments.mockResolvedValue([
      comment({ id: 1, body: "first note", author_label: "Lead" }),
      comment({ id: 2, body: "second note", author_kind: "agent", author_label: "dev-frontend" }),
    ]);

    render(<TaskComments projectId={1} taskId={1005} />);

    expect(await screen.findByText("first note")).toBeInTheDocument();
    expect(screen.getByText("second note")).toBeInTheDocument();
    // Count badge reflects 2 comments.
    const section = document.querySelector("[data-task-comments]");
    expect(section?.getAttribute("data-task-comments-count")).toBe("2");
  });

  it("collapses (no list) when the thread is empty", async () => {
    mockGetTaskComments.mockResolvedValue([]);
    render(<TaskComments projectId={1} taskId={1005} />);

    // Wait for the toggle to settle to collapsed (aria-expanded=false).
    await waitFor(() => {
      const toggle = document.querySelector("[data-task-comments-toggle]");
      expect(toggle?.getAttribute("aria-expanded")).toBe("false");
    });
    expect(document.querySelector("[data-task-comments-list]")).toBeNull();
  });

  it("does NOT execute a <script> embedded in a markdown comment body", async () => {
    mockGetTaskComments.mockResolvedValue([
      comment({
        id: 9,
        body: 'evil <script>window.__xss = true</script> tail',
        body_markdown: true,
      }),
    ]);

    render(<TaskComments projectId={1} taskId={1005} />);
    await screen.findByText(/tail/);

    // No real <script> element rendered; the literal text survives.
    const body = document.querySelector("[data-task-comment-body]");
    expect(body?.querySelector("script")).toBeNull();
    expect(body?.textContent).toContain("<script>");
  });

  it("does NOT render an <img onerror> from a markdown body", async () => {
    mockGetTaskComments.mockResolvedValue([
      comment({ id: 10, body: '<img src=x onerror="boom()">', body_markdown: true }),
    ]);
    render(<TaskComments projectId={1} taskId={1005} />);
    await screen.findByText(/onerror/);

    const body = document.querySelector("[data-task-comment-body]");
    expect(body?.querySelector("img")).toBeNull();
  });

  it("renders a non-markdown body as escaped plain text", async () => {
    mockGetTaskComments.mockResolvedValue([
      comment({ id: 11, body: "plain <b>not bold</b>", body_markdown: false }),
    ]);
    render(<TaskComments projectId={1} taskId={1005} />);
    await screen.findByText(/not bold/);

    const body = document.querySelector("[data-task-comment-body]");
    // body_markdown=false → no <b> element; the angle brackets are literal text.
    expect(body?.querySelector("b")).toBeNull();
    expect(body?.textContent).toContain("<b>not bold</b>");
  });
});

describe("TaskComments — compose", () => {
  it("posts a new comment as author_kind='user' and appends it", async () => {
    mockGetTaskComments.mockResolvedValue([]);
    mockPostTaskComment.mockResolvedValue(
      comment({ id: 99, body: "my new comment", body_markdown: true }),
    );

    render(<TaskComments projectId={1} taskId={1005} authorLabel="bankung" />);

    // Wait for the compose textarea (empty-thread is collapsed but compose lives
    // inside the panel — empty thread auto-collapses, so expand first).
    const toggle = await waitFor(() => {
      const t = document.querySelector("[data-task-comments-toggle]");
      expect(t).not.toBeNull();
      return t as HTMLElement;
    });
    // Empty thread starts collapsed → open it to reach the compose box.
    if (toggle.getAttribute("aria-expanded") === "false") {
      fireEvent.click(toggle);
    }

    const textarea = (await screen.findByPlaceholderText(
      /add a comment/i,
    )) as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: "my new comment" } });

    const submit = document.querySelector(
      "[data-task-comment-submit]",
    ) as HTMLButtonElement;
    fireEvent.click(submit);

    // The post helper was called with author_kind="user" + the typed body.
    await waitFor(() => {
      expect(mockPostTaskComment).toHaveBeenCalledTimes(1);
    });
    const [, , postBody] = mockPostTaskComment.mock.calls[0];
    expect(postBody.author_kind).toBe("user");
    expect(postBody.body).toBe("my new comment");
    expect(postBody.author_label).toBe("bankung");

    // The new row appears in the thread.
    expect(await screen.findByText("my new comment")).toBeInTheDocument();
  });

  it("disables submit on an empty body", async () => {
    mockGetTaskComments.mockResolvedValue([comment({ id: 1, body: "x" })]);
    render(<TaskComments projectId={1} taskId={1005} />);
    await screen.findByText("x");

    const submit = document.querySelector(
      "[data-task-comment-submit]",
    ) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
  });
});
