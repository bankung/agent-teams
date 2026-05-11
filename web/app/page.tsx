import { getProjectByName, listTasks } from "@/lib/api";
import { TaskRunMode } from "@/lib/constants";
import { Board } from "@/components/Board";

export default async function Home() {
  const projectName = process.env.NEXT_PUBLIC_PROJECT_NAME ?? "agent-teams";
  const project = await getProjectByName(projectName);
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
