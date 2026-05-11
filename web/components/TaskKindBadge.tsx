import type { TaskKindValue } from "@/lib/constants";

type Props = { kind: TaskKindValue };

export function TaskKindBadge({ kind }: Props) {
  if (kind === "human") {
    return <span className="text-[11px] text-zinc-500">human</span>;
  }
  return (
    <span className="inline-flex items-center rounded px-1.5 py-0.5 text-[11px] font-medium text-violet-700 bg-violet-50">
      ai
    </span>
  );
}
