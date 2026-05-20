// Inbox — Kanban #1001 AC3. Minimal cross-project list of pending interaction
// tasks (question / decision, not yet DONE / CANCELLED). The page is the
// canonical post-action redirect target after Approve / Reject / Halt on the
// focus page.
//
// Server Component. Mirrors the cross-project aggregation pattern used by
// listAuditFlags() — fetches active projects then per-project pending tasks
// in parallel, client-side filters to interaction_kind != 'work'.
//
// Out of scope for this slice (file as followups):
//   - filtering / sorting / search UI
//   - real-time refresh on new HITL spawns
//   - empty-state CTA to /dashboard

import Link from "next/link";

import { listProjects, listTasks, type ProjectRead, type TaskRead } from "@/lib/api";
import { TaskStatus } from "@/lib/constants";
import { ThemePicker } from "@/components/ThemePicker";

export const dynamic = "force-dynamic";

type InboxEntry = {
  task: TaskRead;
  project: ProjectRead;
};

// Fetch the cross-project inbox. Errors on individual projects degrade to an
// empty list for that project — a single project's API outage shouldn't blank
// the whole inbox for the other N-1 projects (parity with listAuditFlags).
async function fetchInbox(): Promise<InboxEntry[]> {
  const projects = await listProjects({ status: 1 });
  const perProject = await Promise.all(
    projects.map(async (project) => {
      try {
        const tasks = await listTasks(project.id, {
          pending: true,
          limit: 500,
        });
        return tasks
          .filter(
            (t) =>
              t.interaction_kind !== "work" &&
              t.process_status !== TaskStatus.DONE &&
              t.process_status !== TaskStatus.CANCELLED,
          )
          .map<InboxEntry>((task) => ({ task, project }));
      } catch {
        return [];
      }
    }),
  );
  return perProject.flat();
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

export default async function InboxPage() {
  const entries = await fetchInbox();

  return (
    <main
      data-inbox-page
      className="flex min-h-screen flex-col overflow-y-auto bg-white px-4 py-4 sm:px-6 sm:py-5 dark:bg-zinc-950"
    >
      <header className="mb-4 flex flex-wrap items-center gap-2 text-sm">
        <Link
          href="/dashboard"
          className="text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100"
        >
          ← Dashboard
        </Link>
        <span aria-hidden className="text-zinc-300 dark:text-zinc-600">
          ·
        </span>
        <span className="text-base font-semibold text-zinc-900 dark:text-zinc-100">
          Inbox
        </span>
        <span className="ml-1.5 text-[11px] font-normal text-zinc-500 dark:text-zinc-400 tabular-nums">
          ({entries.length})
        </span>
        <span className="ml-auto flex w-full items-center justify-end gap-2 sm:w-auto">
          <ThemePicker />
        </span>
      </header>

      <div className="mx-auto w-full max-w-2xl">
        {entries.length === 0 ? (
          <p
            data-inbox-empty
            className="rounded border border-zinc-200 bg-zinc-50 p-4 text-sm text-zinc-600 dark:border-zinc-800 dark:bg-zinc-900/40 dark:text-zinc-400"
          >
            Nothing waiting. Pending question / decision tasks land here when
            they need a human response.
          </p>
        ) : (
          <ul
            data-inbox-list
            className="flex flex-col gap-2 list-none p-0"
          >
            {entries.map(({ task, project }) => (
              <li key={task.id}>
                <Link
                  href={`/tasks/${task.id}`}
                  data-inbox-task-id={task.id}
                  data-task-interaction-kind={task.interaction_kind}
                  className="flex flex-col gap-1 rounded border border-zinc-200 bg-white p-3 hover:border-zinc-300 hover:bg-zinc-50 dark:border-zinc-800 dark:bg-zinc-900 dark:hover:border-zinc-700 dark:hover:bg-zinc-900/70"
                >
                  <div className="flex flex-wrap items-center gap-2 text-xs">
                    <span className="font-mono text-zinc-500 dark:text-zinc-400">
                      #{task.id}
                    </span>
                    <span
                      data-task-interaction-kind={task.interaction_kind}
                      className="rounded bg-violet-50 px-1.5 py-0.5 font-mono uppercase tracking-wide text-violet-700 dark:bg-violet-900/30 dark:text-violet-200"
                    >
                      {task.interaction_kind}
                    </span>
                    <span className="text-zinc-500 dark:text-zinc-400">
                      in {project.name}
                    </span>
                  </div>
                  <h2 className="text-sm font-medium text-zinc-900 dark:text-zinc-100">
                    {truncate(task.title, 100)}
                  </h2>
                  {task.question_payload?.question && (
                    <p className="text-xs text-zinc-600 dark:text-zinc-400">
                      {truncate(task.question_payload.question, 140)}
                    </p>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </main>
  );
}
