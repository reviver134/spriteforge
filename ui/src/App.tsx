import { useQuery } from "@tanstack/react-query";

import { getJson, type Run, type RunList } from "./api";

function runTitle(run: Run): string {
  const goal = run.config.goal?.trim();
  return goal || `Untitled project ${run.id.slice(0, 6)}`;
}

function ProjectList() {
  const { data, error, isPending } = useQuery({
    queryKey: ["runs"],
    queryFn: () => getJson<RunList>("/api/runs"),
  });
  if (isPending) return <p className="muted">Loading projects…</p>;
  if (error) return <p className="error">{error.message}</p>;
  const runs = data.runs.filter((r) => r.config.workflow !== "router-eval");
  if (!runs.length) return <p className="muted">No projects yet.</p>;
  return (
    <ul className="projects" aria-label="Projects">
      {runs.map((run) => (
        <li key={run.id}>
          <a href={`/classic?run=${encodeURIComponent(run.id)}`}>
            <span className="project-title">{runTitle(run)}</span>
            <span className={`status status-${run.status}`}>{run.status.replace("_", " ")}</span>
          </a>
        </li>
      ))}
    </ul>
  );
}

export function App() {
  return (
    <main className="home">
      <header>
        <h1>SpriteForge Studio</h1>
        <p className="muted">
          Describe a game; the agents build it with you. <a href="/classic">Classic view</a>
        </p>
      </header>
      <section>
        <h2>Projects</h2>
        <ProjectList />
      </section>
    </main>
  );
}
