import { notFound } from "next/navigation";

import { getProjectByName, listTasks, HttpError } from "@/lib/api";
import { TaskRunMode } from "@/lib/constants";
import { Board } from "@/components/Board";

type Props = { params: { name: string } };

export default async function ProjectBoardPage({ params }: Props) {
  let project;
  try {
    project = await getProjectByName(params.name);
  } catch (e) {
    // Only 404 → Next.js notFound page. Everything else (500, network outage,
    // 422 from a future contract drift) re-throws and lands in app/error.tsx —
    // symmetric with the listTasks call below. Pre-#760 a bare catch swallowed
    // all errors as 404, hiding backend outages behind "wrong project name".
    if (e instanceof HttpError && e.status === 404) notFound();
    throw e;
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
