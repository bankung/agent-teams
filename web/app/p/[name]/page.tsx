import { notFound } from "next/navigation";

import {
  getProjectByName,
  getProjectsStats,
  getProjectProgressStats,
  listAllTasks,
  listDoneLanePage,
  HttpError,
} from "@/lib/api";
import { TaskRunMode } from "@/lib/constants";
import { Board } from "@/components/Board";

type Props = { params: Promise<{ name: string }> };

export default async function ProjectBoardPage(props: Props) {
  const params = await props.params;
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
  // Kanban #2112 — heap-win: split active vs DONE into two fetches.
  // active: listAllTasks(pending=true) — excludes DONE + CANCELLED, paginates
  //   fully (projects with >500 active tasks get the complete set).
  // doneFirstPage: listDoneLanePage(limit=50) — first 50 DONE tasks only;
  //   remaining pages fetched client-side on "Load more".
  // Previously: listAllTasks(project.id) fetched ALL tasks incl. every DONE.
  const [active, doneFirstPage, projectStats, progressStats] = await Promise.all([
    listAllTasks(project.id, { pending: true }),
    listDoneLanePage(project.id, { limit: 50 }),
    getProjectsStats({ projectId: project.id }),
    getProjectProgressStats(project.id),
  ]);
  const initialTasks = [...active, ...doneFirstPage];
  // has-more: if the first page is exactly the limit, the server likely has more.
  const initialDoneHasMore = doneFirstPage.length === 50;
  const hasHeadlessTask = initialTasks.some(
    (t) => t.run_mode === TaskRunMode.AUTO_HEADLESS,
  );

  return (
    <Board
      initialTasks={initialTasks}
      initialDoneHasMore={initialDoneHasMore}
      hasHeadlessTask={hasHeadlessTask}
      project={project}
      projectStats={projectStats}
      progressStats={progressStats}
    />
  );
}
