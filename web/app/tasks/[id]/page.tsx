// Task focus page — Kanban #1001. New top-level surface for push-notification
// quick-actions. Deep-link target: /tasks/{id}?action_hint=approve|reject.
//
// Architecture: Server parent (this file) + Client island (TaskFocusView).
// Server fetches the task + its project on the request hot-path (cheap; both
// reads are unguarded GETs). 404 on the task → Next.js notFound. Any other
// error bubbles to app/error.tsx (symmetric with /p/[name]/page.tsx per
// context/standards/nextjs/typed-error-catch.md).
//
// X-Project-Id problem: GET /api/tasks/{id} requires the header (Kanban #695),
// but the URL only carries the task id. We solve this with a small probe loop —
// list active projects, then for each try `getTask(project.id, taskId)` and keep
// the one that succeeds. The volume of projects on this instance is tiny (~10s);
// the probe is acceptable for v1. If volume grows, swap to a future BE
// `/api/tasks/{id}/by-id` no-header endpoint or a session-bound cookie.

import Link from "next/link";
import { notFound } from "next/navigation";

import {
  getProjectById,
  getTask,
  HttpError,
  listProjects,
  type ProjectRead,
  type TaskRead,
} from "@/lib/api";
import { TaskFocusView } from "@/components/TaskFocusView";

export const dynamic = "force-dynamic";

type Props = {
  params: Promise<{ id: string }>;
  searchParams?: Promise<{ action_hint?: string }>;
};

// Locate the task across the bound user's active projects. The X-Project-Id
// header is the gate — we discover the right value by probing.
async function locateTask(
  taskId: number,
): Promise<{ task: TaskRead; project: ProjectRead } | null> {
  const projects = await listProjects({ status: 1 });

  // S-3: fan out all project probes concurrently instead of serially.
  // 400/404 from a wrong project → filtered out (same semantics as the old
  // `continue`). Non-HttpError or unexpected status → re-thrown so it bubbles
  // to error.tsx exactly as before.
  const results = await Promise.allSettled(
    projects.map(async (project) => {
      try {
        const task = await getTask(project.id, taskId);
        return { task, project };
      } catch (err) {
        if (err instanceof HttpError && (err.status === 400 || err.status === 404)) {
          return null; // task not in this project — filter below
        }
        throw err; // unexpected — surface to allSettled as rejected
      }
    }),
  );

  for (const result of results) {
    if (result.status === "rejected") throw result.reason;
    if (result.value !== null) return result.value;
  }
  return null;
}

export default async function TaskFocusPage(props: Props) {
  const searchParams = await props.searchParams;
  const params = await props.params;
  const parsedId = Number.parseInt(params.id, 10);
  if (!Number.isFinite(parsedId) || parsedId < 1) {
    notFound();
  }

  // If the user navigated directly to /tasks/{id} (e.g. via push click), we
  // don't have a project context. Probe to find it.
  const located = await locateTask(parsedId);
  if (!located) {
    notFound();
  }

  // Defensive re-fetch of the project (the listProjects call returned the
  // active-status set; if the row was mutated after the probe a fresh read
  // is more accurate). Cheap GET; tolerable.
  let project = located.project;
  try {
    project = await getProjectById(project.id);
  } catch (err) {
    if (!(err instanceof HttpError) || err.status !== 404) throw err;
    // Project deleted between the probe and the re-fetch — fall through with
    // the stale row; the task focus view still works for the action PATCH.
  }

  // Normalize action_hint to the locked enum (approve | reject). Anything
  // else is silently dropped so a typo / stale URL doesn't crash the page.
  const rawHint = searchParams?.action_hint;
  const actionHint: "approve" | "reject" | null =
    rawHint === "approve" || rawHint === "reject" ? rawHint : null;

  return (
    <main
      data-task-focus-page
      data-task-id={located.task.id}
      className="glass-board flex min-h-screen flex-col overflow-y-auto bg-white px-4 py-4 sm:px-6 sm:py-5 dark:bg-zinc-950"
    >
      <header className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        <Link
          href="/inbox"
          className="text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
        >
          ← Inbox
        </Link>
        <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
          ·
        </span>
        <span className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
          Task #{located.task.id}
        </span>
      </header>

      <div className="mx-auto w-full max-w-2xl pb-24 sm:pb-4">
        <TaskFocusView
          task={located.task}
          project={project}
          actionHint={actionHint}
        />
      </div>
    </main>
  );
}
