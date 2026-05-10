import { getProjectByName, listTasks, type TaskRead } from "@/lib/api";
import {
  TaskRunMode,
  TaskStatus,
  type TaskStatusValue,
} from "@/lib/constants";
import { BoardColumn } from "@/components/BoardColumn";
import { ProjectConsentBanner } from "@/components/ProjectConsentBanner";

const COLUMNS: Array<{ status: TaskStatusValue; label: string }> = [
  { status: TaskStatus.TODO, label: "Todo" },
  { status: TaskStatus.IN_PROGRESS, label: "In progress" },
  { status: TaskStatus.REVIEW, label: "Review" },
  { status: TaskStatus.BLOCKED, label: "Blocked" },
  { status: TaskStatus.DONE, label: "Done" },
];

function groupByStatus(tasks: TaskRead[]) {
  const groups = new Map<TaskStatusValue, TaskRead[]>();
  for (const col of COLUMNS) groups.set(col.status, []);
  for (const task of tasks) {
    const bucket = groups.get(task.process_status);
    if (bucket) bucket.push(task);
  }
  for (const bucket of groups.values()) {
    bucket.sort((a, b) => b.priority - a.priority || a.id - b.id);
  }
  return groups;
}

export default async function Home() {
  const projectName = process.env.NEXT_PUBLIC_PROJECT_NAME ?? "agent-teams";
  const project = await getProjectByName(projectName);
  const tasks = await listTasks(project.id);
  const grouped = groupByStatus(tasks);
  const hasHeadlessTask = tasks.some(
    (t) => t.run_mode === TaskRunMode.AUTO_HEADLESS,
  );

  return (
    <main className="min-h-screen bg-white px-6 py-5">
      <header className="mb-4 flex flex-col gap-2">
        <div className="flex items-baseline gap-2 text-sm">
          <h1 className="text-xl font-semibold tracking-tight text-zinc-900">
            {project.name}
          </h1>
          <span aria-hidden className="text-zinc-300">
            ·
          </span>
          <span className="text-zinc-600">
            team: <span className="text-zinc-900">{project.team}</span>
          </span>
          <span aria-hidden className="text-zinc-300">
            ·
          </span>
          <span className="text-zinc-500 tabular-nums">
            {tasks.length} task{tasks.length === 1 ? "" : "s"}
          </span>
        </div>
        <ProjectConsentBanner
          project={project}
          hasHeadlessTask={hasHeadlessTask}
        />
      </header>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3 lg:grid-cols-5">
        {COLUMNS.map((col) => (
          <BoardColumn
            key={col.status}
            status={col.status}
            label={col.label}
            tasks={grouped.get(col.status) ?? []}
          />
        ))}
      </div>
    </main>
  );
}
