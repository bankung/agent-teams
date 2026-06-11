"use client";

// TaskComments — Kanban #1005 (AC#5 thread UI + AC#6 markdown render).
//
// Append-only comment thread rendered at the BOTTOM of TaskDetail. The thread is
// collapsible: default-EXPANDED when the task has >0 comments, collapsed when 0.
// Rows render oldest→newest top-to-bottom (chronological, matching the BE's
// id-ASC ordering and the existing answer-history section). A compose box posts
// new comments as author_kind="user"; "Load older" walks the `before` cursor.
//
// SECURITY (AC#6): comment bodies are attacker/agent-controllable. When
// body_markdown=true we render via lib/safeMarkdown.renderMarkdown — a renderer
// that produces React elements only (NEVER dangerouslySetInnerHTML), so raw HTML
// like <script> / <img onerror=…> is shown as escaped literal text, never DOM.
// When body_markdown=false we render plain text (React-escaped) preserving
// whitespace. See lib/safeMarkdown.tsx for the full threat model.

import { useEffect, useState } from "react";

import {
  COMMENT_AUTHOR_LABEL_MAX,
  COMMENT_BODY_MAX,
  getTaskComments,
  postTaskComment,
  type CommentAuthorKindValue,
  type TaskCommentRead,
} from "@/lib/api";
import { extractErrorMessage } from "@/lib/errors";
import { renderMarkdown } from "@/lib/safeMarkdown";
import { formatRelative } from "@/lib/time";

type Props = {
  projectId: number;
  taskId: number;
  // Operator attribution stamped on posted comments. Defaults to "operator"
  // (single-operator dev mode); a future multi-operator UI threads the real
  // name through here.
  authorLabel?: string;
};

// Page size for the initial load + each "load older" fetch. The BE caps at 200;
// 50 is the BE default and a comfortable chat-thread page.
const PAGE = 50;

// author_kind → chip palette. Mirrors the zinc/violet/blue tones used across the
// board (TaskKindBadge, TaskToolCalls tiers).
const AUTHOR_CLASS: Record<CommentAuthorKindValue, string> = {
  user: "bg-violet-50 text-violet-800 dark:bg-violet-900/30 dark:text-violet-200",
  agent: "bg-blue-50 text-blue-800 dark:bg-blue-900/30 dark:text-blue-200",
  system: "bg-zinc-100 text-zinc-600 dark:bg-zinc-800 dark:text-zinc-300",
};

export function TaskComments({ projectId, taskId, authorLabel = "operator" }: Props) {
  // null = first load not yet resolved; [] = resolved-empty.
  const [comments, setComments] = useState<TaskCommentRead[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);
  // hasMore: a full page came back → an older page may exist. Reset on each task.
  const [hasMore, setHasMore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);

  // Initial load — newest 50 via a single page (no cursor = oldest-first from
  // the BE; we request a page and treat a full page as "older pages may exist").
  // Because the BE returns oldest-first, the FIRST page is the OLDEST 50. For a
  // small thread (the common case) that is the whole thread. "Load older" is a
  // no-op until the thread exceeds one page, at which point the oldest id of the
  // loaded set is the cursor — see handleLoadOlder. (Trade-off documented in the
  // role report: load-all-small-threads, paginate-from-oldest is the simple-
  // correct choice given the BE's id-ASC + `before` cursor semantics.)
  useEffect(() => {
    let cancelled = false;
    setComments(null);
    setLoadError(null);
    setHasMore(false);
    getTaskComments(projectId, taskId, { limit: PAGE })
      .then((rows) => {
        if (cancelled) return;
        setComments(rows);
        setHasMore(rows.length === PAGE);
        // Collapse only when the thread is empty; expand when it has content.
        setCollapsed(rows.length === 0);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setLoadError(extractErrorMessage(err, "Failed to load comments"));
        setComments([]);
        setCollapsed(false);
      });
    return () => {
      cancelled = true;
    };
  }, [projectId, taskId]);

  const handleLoadOlder = async () => {
    if (loadingOlder || comments === null || comments.length === 0) return;
    const oldestId = comments[0].id; // rows are id-ASC; [0] is the oldest loaded
    setLoadingOlder(true);
    try {
      const older = await getTaskComments(projectId, taskId, {
        before: oldestId,
        limit: PAGE,
      });
      // Prepend older rows (they precede the current set chronologically).
      setComments((prev) => (prev ? [...older, ...prev] : older));
      setHasMore(older.length === PAGE);
    } catch (err: unknown) {
      setLoadError(extractErrorMessage(err, "Failed to load older comments"));
    } finally {
      setLoadingOlder(false);
    }
  };

  // Append a freshly-posted comment (newest → end of the id-ASC list).
  const handlePosted = (created: TaskCommentRead) => {
    setComments((prev) => (prev ? [...prev, created] : [created]));
    setCollapsed(false); // a just-posted comment should be visible
  };

  // Loading placeholder — stable layout while the first page is in flight.
  if (comments === null && loadError === null) {
    return (
      <section
        className="flex flex-col gap-2"
        data-task-comments
        data-task-comments-state="loading"
      >
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Comments
        </h3>
        <p className="text-xs italic text-zinc-400 dark:text-zinc-500">…</p>
      </section>
    );
  }

  const count = comments?.length ?? 0;

  return (
    <section
      className="flex flex-col gap-2"
      data-task-comments
      data-task-comments-count={count}
    >
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        aria-expanded={!collapsed}
        data-task-comments-toggle
        className="flex w-full items-center gap-2 text-left"
      >
        <span
          aria-hidden
          className={`inline-block text-zinc-400 transition-transform dark:text-zinc-500 ${
            collapsed ? "" : "rotate-90"
          }`}
        >
          <svg
            width="10"
            height="10"
            viewBox="0 0 10 10"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M3 1.5 L7 5 L3 8.5" />
          </svg>
        </span>
        <h3 className="text-xs font-semibold uppercase tracking-wide text-zinc-500 dark:text-zinc-400">
          Comments{count > 0 ? ` (${count})` : ""}
        </h3>
      </button>

      {loadError !== null && (
        <p
          data-task-comments-error
          className="rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700 dark:border-red-900 dark:bg-red-900/30 dark:text-red-300"
        >
          {loadError}
        </p>
      )}

      {!collapsed && (
        <div className="flex flex-col gap-3" data-task-comments-panel>
          {hasMore && (
            <button
              type="button"
              onClick={handleLoadOlder}
              disabled={loadingOlder}
              data-task-comments-load-older
              className="self-start rounded border border-zinc-200 bg-white px-2 py-1 text-xs font-medium text-zinc-600 hover:border-zinc-300 hover:text-zinc-900 disabled:opacity-50 dark:border-zinc-800 dark:bg-zinc-900 dark:text-zinc-400 dark:hover:border-zinc-700 dark:hover:text-zinc-100"
            >
              {loadingOlder ? "Loading…" : "Load older"}
            </button>
          )}

          {count === 0 ? (
            <p className="text-sm italic text-zinc-500 dark:text-zinc-400">
              No comments yet.
            </p>
          ) : (
            <ol className="flex flex-col gap-2" data-task-comments-list>
              {comments!.map((c) => (
                <CommentItem key={c.id} comment={c} />
              ))}
            </ol>
          )}

          <CommentCompose
            projectId={projectId}
            taskId={taskId}
            authorLabel={authorLabel}
            onPosted={handlePosted}
          />
        </div>
      )}
    </section>
  );
}

// CommentItem — one comment row: author chip + relative time + rendered body.
function CommentItem({ comment }: { comment: TaskCommentRead }) {
  const authorClass = AUTHOR_CLASS[comment.author_kind] ?? AUTHOR_CLASS.system;
  return (
    <li
      className="rounded border border-zinc-200 bg-zinc-50 p-2 dark:border-zinc-800 dark:bg-zinc-950/40"
      data-task-comment
      data-task-comment-id={comment.id}
      data-task-comment-kind={comment.author_kind}
    >
      <div className="mb-1 flex items-center gap-2">
        <span
          className={`inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${authorClass}`}
          data-task-comment-author-kind
        >
          {comment.author_kind}
        </span>
        {comment.author_label && (
          <span
            className="truncate text-xs font-medium text-zinc-700 dark:text-zinc-300"
            data-task-comment-author-label
          >
            {comment.author_label}
          </span>
        )}
        <span
          className="ml-auto shrink-0 font-mono text-[11px] text-zinc-500 dark:text-zinc-400"
          title={comment.created_at}
          data-task-comment-time
        >
          {formatRelative(comment.created_at)}
        </span>
      </div>
      <div
        className="text-sm text-zinc-900 dark:text-zinc-100"
        data-task-comment-body
        data-task-comment-markdown={comment.body_markdown ? "true" : "false"}
      >
        {comment.body_markdown ? (
          // SAFE: renderMarkdown emits React elements only — no HTML sink.
          renderMarkdown(comment.body)
        ) : (
          // Plain-text: React escapes; whitespace-pre-wrap preserves shape.
          <p className="whitespace-pre-wrap break-words">{comment.body}</p>
        )}
      </div>
    </li>
  );
}

// CommentCompose — textarea + submit. Responsive (stacks on mobile, inline
// submit on desktop). Disables submit on empty/over-cap body; serializes posts
// behind `posting` so the BE 30/min rate limit is never tripped by one operator.
function CommentCompose({
  projectId,
  taskId,
  authorLabel,
  onPosted,
}: {
  projectId: number;
  taskId: number;
  authorLabel: string;
  onPosted: (created: TaskCommentRead) => void;
}) {
  const [body, setBody] = useState("");
  const [posting, setPosting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const trimmed = body.trim();
  const overCap = body.length > COMMENT_BODY_MAX;
  const canSubmit = !posting && trimmed.length > 0 && !overCap;

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setPosting(true);
    setError(null);
    try {
      const created = await postTaskComment(projectId, taskId, {
        author_kind: "user",
        author_label: authorLabel.slice(0, COMMENT_AUTHOR_LABEL_MAX),
        body: trimmed,
        body_markdown: true,
      });
      setBody("");
      onPosted(created);
    } catch (err: unknown) {
      setError(extractErrorMessage(err, "Failed to post comment"));
    } finally {
      setPosting(false);
    }
  };

  return (
    <div
      className="flex flex-col gap-1.5 rounded border border-zinc-200 bg-white p-2 dark:border-zinc-800 dark:bg-zinc-900"
      data-task-comment-compose
    >
      <label htmlFor={`comment-body-${taskId}`} className="sr-only">
        Add a comment
      </label>
      <textarea
        id={`comment-body-${taskId}`}
        value={body}
        onChange={(e) => setBody(e.target.value)}
        // Cmd/Ctrl+Enter submits — keyboard parity with chat composers.
        onKeyDown={(e) => {
          if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
            e.preventDefault();
            void handleSubmit();
          }
        }}
        disabled={posting}
        rows={3}
        placeholder="Add a comment… (markdown supported)"
        data-task-comment-textarea
        className="w-full resize-y rounded border border-zinc-300 bg-white px-2 py-1.5 text-sm text-zinc-900 placeholder:text-zinc-400 focus:border-zinc-500 focus:outline-none disabled:opacity-50 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-100"
      />
      {overCap && (
        <p className="text-xs text-red-600 dark:text-red-400" data-task-comment-overcap>
          Too long — {body.length.toLocaleString()} / {COMMENT_BODY_MAX.toLocaleString()} characters.
        </p>
      )}
      {error !== null && (
        <p
          className="rounded border border-red-200 bg-red-50 px-2 py-1 text-xs text-red-700 dark:border-red-900 dark:bg-red-900/30 dark:text-red-300"
          data-task-comment-compose-error
        >
          {error}
        </p>
      )}
      {/* Responsive action row: full-width 44px tap target on mobile, compact
          right-aligned on desktop. */}
      <div className="flex flex-col gap-1.5 sm:flex-row sm:items-center sm:justify-end">
        <button
          type="button"
          onClick={handleSubmit}
          disabled={!canSubmit}
          data-task-comment-submit
          className="min-h-[44px] w-full rounded border border-violet-300 bg-violet-600 px-4 py-2 text-sm font-medium text-white hover:bg-violet-700 disabled:opacity-50 sm:min-h-0 sm:w-auto sm:px-3 sm:py-1.5 dark:border-violet-700 dark:bg-violet-700 dark:hover:bg-violet-600"
        >
          {posting ? "Posting…" : "Comment"}
        </button>
      </div>
    </div>
  );
}
