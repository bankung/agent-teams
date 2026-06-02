import { notFound } from "next/navigation";

import {
  getProjectByName,
  getProjectsStats,
  getProjectProgressStats,
  listTasks,
  HttpError,
} from "@/lib/api";
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
  // Kanban #1289 — fetch per-project stats in parallel with tasks.
  // BE returns an array of length 0 or 1 when project_id is given.
  // Kanban #1292 — burndown + velocity series SSR-fetched in the same
  // Promise.all (established pattern; avoids the client-fetch origin class
  // that bit #1673). Defaults: bucket=week, days=90 (the BE defaults).
  const [tasks, projectStats, progressStats] = await Promise.all([
    listTasks(project.id, { limit: 500 }),
    getProjectsStats({ projectId: project.id }),
    getProjectProgressStats(project.id),
  ]);
  const hasHeadlessTask = tasks.some(
    (t) => t.run_mode === TaskRunMode.AUTO_HEADLESS,
  );

  return (
    <Board
      initialTasks={tasks}
      hasHeadlessTask={hasHeadlessTask}
      project={project}
      projectStats={projectStats}
      progressStats={progressStats}
    />
  );
}
