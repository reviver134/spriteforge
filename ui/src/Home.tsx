import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { artifactUrl, getJson, postJson, type Project, type Setup } from "./api";
import { go, href, navClick } from "./nav";

function SetupNotice({ setup }: { setup: Setup }) {
  const notes = [];
  if (!setup.meta_api_key) {
    notes.push(
      "No META_API_KEY is set, so Muse can't be called. Put your key in a .env file in the " +
        "repo folder and restart, or try the whole flow offline with stand-in models.",
    );
  }
  if (!setup.chromium) {
    notes.push(
      "Chromium for Playwright is missing, so builds can't be played or checked. Run: " +
        "python -m playwright install chromium",
    );
  }
  if (!notes.length) return null;
  return (
    <div className="notice" role="status">
      {notes.map((n) => (
        <p key={n}>{n}</p>
      ))}
    </div>
  );
}

function NewProject({ setup }: { setup: Setup | undefined }) {
  const [idea, setIdea] = useState("");
  const client = useQueryClient();
  const start = useMutation({
    mutationFn: (mock: boolean) =>
      postJson<Project>("/api/projects", { text: idea.trim(), mock }),
    onSuccess: (project) => {
      setIdea("");
      client.invalidateQueries({ queryKey: ["projects"] });
      go({ project: project.id }); // straight into the Director's chat
    },
  });
  // Until the key check answers, neither mode is offered (a live key must not start mock).
  const live = setup?.meta_api_key ?? false;
  const ready = setup !== undefined && idea.trim().length > 0 && !start.isPending;
  return (
    <form
      className="new-project"
      onSubmit={(e) => {
        e.preventDefault();
        if (ready) start.mutate(!live);
      }}
    >
      <textarea
        aria-label="Describe your game"
        placeholder="Describe your game..."
        value={idea}
        rows={4}
        onChange={(e) => setIdea(e.target.value)}
        onKeyDown={(e) => {
          // Enter starts (Muse when the key is set), Shift+Enter is a new line.
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            if (ready) start.mutate(!live);
          }
        }}
      />
      <div className="actions">
        {live && (
          <button type="submit" disabled={!ready}>
            Start with Muse
          </button>
        )}
        {setup !== undefined && (
          <button
            type={live ? "button" : "submit"}
            className={live ? "secondary" : undefined}
            disabled={!ready}
            onClick={live ? () => ready && start.mutate(true) : undefined}
          >
            Try offline (mock)
          </button>
        )}
      </div>
      {setup === undefined && <p className="muted">Checking for your Muse key…</p>}
      {start.isPending && <p className="muted">Starting…</p>}
      {start.error && <p className="error">{start.error.message}</p>}
    </form>
  );
}

function statusText(p: Project): string {
  if (p.gate) return `Needs you: ${p.gate.title.split(":")[0]}`;
  if (p.busy) return "Working…";
  return p.status.replace("_", " ");
}

function ProjectList() {
  const { data, error, isPending } = useQuery({
    queryKey: ["projects"],
    queryFn: () => getJson<{ projects: Project[] }>("/api/projects"),
    // Projects move on by themselves while their agents work.
    refetchInterval: (q) => (q.state.data?.projects.some((p) => p.busy) ? 1500 : false),
  });
  if (isPending) return <p className="muted">Loading projects…</p>;
  if (error) return <p className="error">{error.message}</p>;
  if (!data.projects.length) return <p className="muted">No projects yet.</p>;
  return (
    <ul className="projects" aria-label="Projects">
      {data.projects.map((p) => (
        <li key={p.id}>
          <a href={href({ project: p.id })} onClick={navClick({ project: p.id })}>
            <span className="thumb">
              {p.thumbnail && <img src={artifactUrl(p.thumbnail)} alt="" />}
            </span>
            <span className="project-text">
              <span className="project-title">{p.title}</span>
              <span className={`status status-${p.gate ? "gate" : p.status}`}>
                {statusText(p)}
              </span>
            </span>
            <span className="cost" title="Estimated cost of the model calls">
              {p.mock ? "offline" : `$${p.cost.toFixed(2)}`}
            </span>
          </a>
        </li>
      ))}
    </ul>
  );
}

export function Home() {
  const setup = useQuery({ queryKey: ["setup"], queryFn: () => getJson<Setup>("/api/setup") });
  return (
    <main className="home">
      <header>
        <h1>SpriteForge Studio</h1>
        <p className="muted">
          Describe a game; the agents build it with you. <a href="/classic">Classic view</a>
        </p>
      </header>
      {setup.data && <SetupNotice setup={setup.data} />}
      <NewProject setup={setup.data} />
      <section>
        <h2>Projects</h2>
        <ProjectList />
      </section>
    </main>
  );
}
