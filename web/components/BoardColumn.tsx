import type { TaskRead } from "@/lib/api";
import type { TaskStatusValue } from "@/lib/constants";
import { TaskCard } from "./TaskCard";

type Props = {
  status: TaskStatusValue;
  label: string;
  tasks: TaskRead[];
};

export function BoardColumn({ status, label, tasks }: Props) {
  return (
    <section
      data-process-status={status}
      className="flex min-w-0 flex-col gap-2 rounded-lg bg-zinc-50 p-3"
    >
      <header className="flex items-center justify-between px-1 text-sm font-semibold text-zinc-700">
        <span>{label}</span>
        <span className="rounded bg-zinc-200 px-1.5 py-0.5 text-xs font-medium text-zinc-700">
          {tasks.length}
        </span>
      </header>
      <div className="flex flex-col gap-2">
        {tasks.length === 0 ? (
          <p className="px-1 py-4 text-center text-xs text-zinc-400">
            No tasks
          </p>
        ) : (
          tasks.map((task) => <TaskCard key={task.id} task={task} />)
        )}
      </div>
    </section>
  );
}
