// Inbox — Kanban #1001 AC3 + #1000. Cross-project list of pending interaction
// tasks (question / decision, not yet DONE / CANCELLED), GROUPED BY PROJECT.
// The page is the canonical post-action redirect target after Approve /
// Reject / Halt on the focus page, and the destination of the dashboard
// InboxBadge (which carries the live count + color thresholds).
//
// Server Component. Mirrors the cross-project aggregation pattern used by
// listAuditFlags() — fetches active projects then per-project pending tasks
// in parallel, client-side filters to interaction_kind != 'work'.
//
// Deep-link target: each row links to /tasks/{id} — the TaskFocusView surface
// (Kanban #1001), a purpose-built answer/decide/approve-reject UI for a single
// task. We deliberately deep-link there rather than /p/{name}?task={id}: the
// latter only highlights + scrolls the board card (the operator must then
// click it to open the drawer), whereas /tasks/{id} lands the operator
// directly on the answer/decide controls. The inbox is a router into the
// EXISTING answer flow — it does not rebuild the answer UI inline (v1).
//
// #1000 additions over the #1001 slice: group-by-project headers + per-row age
// (oldest-first within each project, since the oldest pending HITL is the most
// urgent). Sort key: (project_name ASC, created_at ASC).
//
// Out of scope for this slice (file as followups):
//   - filtering / sorting / search UI
//   - real-time refresh on new HITL spawns (badge already SSE-refreshes)
//   - bulk approve / decide actions

import Link from "next/link";

import { listProjects, listTasks, type ProjectRead, type TaskRead } from "@/lib/api";
import { TaskStatus } from "@/lib/constants";
import { formatRelative } from "@/lib/time";

export const dynamic = "force-dynamic";

type InboxEntry = {
  task: TaskRead;
  project: ProjectRead;
};

type InboxGroup = {
  project: ProjectRead;
  entries: InboxEntry[];
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

// Group entries by project. Sort first by (project_name ASC, created_at ASC)
// so a single forward scan groups adjacent same-project rows (mirrors the
// CrossProjectActiveTasksList grouping approach) and the oldest pending task
// per project — the most urgent — surfaces at the top of its group.
function groupByProject(entries: InboxEntry[]): InboxGroup[] {
  const sorted = [...entries].sort((a, b) => {
    const nameCmp = a.project.name.localeCompare(b.project.name);
    if (nameCmp !== 0) return nameCmp;
    return a.task.created_at.localeCompare(b.task.created_at);
  });
  const groups: InboxGroup[] = [];
  for (const entry of sorted) {
    const last = groups[groups.length - 1];
    if (last && last.project.id === entry.project.id) {
      last.entries.push(entry);
    } else {
      groups.push({ project: entry.project, entries: [entry] });
    }
  }
  return groups;
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

export default async function InboxPage() {
  const entries = await fetchInbox();
  const groups = groupByProject(entries);

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
          <div data-inbox-list className="flex flex-col gap-4">
            {groups.map((group) => (
              <section
                key={group.project.id}
                data-inbox-group
                data-project-id={group.project.id}
                className="flex flex-col rounded border border-zinc-200 dark:border-zinc-800"
              >
                {/* Project header — name links to its board; count on the right. */}
                <header className="flex items-center gap-2 border-b border-zinc-200 bg-zinc-50/60 px-3 py-1.5 dark:border-zinc-800 dark:bg-zinc-950/40">
                  <Link
                    href={`/p/${encodeURIComponent(group.project.name)}`}
                    className="truncate text-xs font-semibold text-zinc-900 hover:underline dark:text-zinc-100"
                  >
                    {group.project.name}
                  </Link>
                  <span className="inline-flex shrink-0 items-center rounded bg-zinc-100 px-1 py-0.5 text-[10px] font-medium uppercase tracking-wide text-zinc-600 dark:bg-zinc-800 dark:text-zinc-400">
                    {group.project.team}
                  </span>
                  <span
                    className="ml-auto text-[11px] text-zinc-500 dark:text-zinc-400 tabular-nums"
                    title={`${group.entries.length} pending interaction${group.entries.length === 1 ? "" : "s"} in this project`}
                  >
                    {group.entries.length}
                  </span>
                </header>

                <ul role="list" className="flex flex-col">
                  {group.entries.map(({ task }) => (
                    <li
                      key={task.id}
                      className="border-t border-zinc-100 first:border-t-0 dark:border-zinc-800"
                    >
                      <Link
                        href={`/tasks/${task.id}`}
                        data-inbox-task-id={task.id}
                        data-task-interaction-kind={task.interaction_kind}
                        className="flex flex-col gap-1 px-3 py-2.5 hover:bg-zinc-50 dark:hover:bg-zinc-900/60"
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
                          <span
                            className="ml-auto shrink-0 text-[11px] text-zinc-500 dark:text-zinc-400 tabular-nums"
                            title={task.created_at}
                          >
                            {formatRelative(task.created_at)}
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
              </section>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
