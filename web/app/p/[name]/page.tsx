import { notFound } from "next/navigation";

import { getProjectByName, listTasks } from "@/lib/api";
import { TaskRunMode } from "@/lib/constants";
import { Board } from "@/components/Board";

type Props = { params: { name: string } };

export default async function ProjectBoardPage({ params }: Props) {
  let project;
  try {
    project = await getProjectByName(params.name);
  } catch {
    // getProjectByName throws on 404 (and other non-2xx); render Next's 404 page.
    notFound();
  }
  const tasks = await listTasks(project.id, { limit: 500 });
  const hasHeadlessTask = tasks.some(
    (t) => t.run_mode === TaskRunMode.AUTO_HEADLESS,
  );

  return (
    <Board
      initialTasks={tasks}
      hasHeadlessTask={hasHeadlessTask}
      project={project}
    />
  );
}
