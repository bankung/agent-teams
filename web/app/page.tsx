import { redirect } from "next/navigation";

export default function Home() {
  const projectName = process.env.NEXT_PUBLIC_PROJECT_NAME ?? "agent-teams";
  redirect(`/p/${projectName}`);
}
