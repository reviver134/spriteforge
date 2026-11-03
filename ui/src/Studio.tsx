import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { AgentList } from "./AgentList";
import { Chat } from "./Chat";
import { getJson, postJson, type Project, type RunAgents } from "./api";
import { href, navClick } from "./nav";
import { useRunEvents } from "./useRunEvents";

const money = (n: number) => `$${n.toFixed(2)}`;

function CostPanel({ state, agent, mock }: { state: RunAgents; agent: string; mock: boolean }) {
  const total = state.agents.reduce(
    (t, a) => ({ cost: t.cost + a.cost, calls: t.calls + a.calls, images: t.images + a.images }),
    { cost: 0, calls: 0, images: 0 },
  );
  const mine = state.agents.find((a) => a.name === agent);
  return (
    <section className="panel" aria-label="Cost">
      <h2>Cost</h2>
      {mock && (
        <p className="muted">Offline: stand-in models, nothing is charged. Costs are estimates.</p>
      )}
      <dl>
        <dt>Project</dt>
        <dd>{money(total.cost)}</dd>
        <dt>Model calls</dt>
        <dd>{total.calls}</dd>
        <dt>Images</dt>
        <dd>{total.images}</dd>
      </dl>
      {mine && (
        <>
          <h3>{agent}</h3>
          <dl>
            <dt>Cost</dt>
            <dd>{money(mine.cost)}</dd>
            <dt>Calls</dt>
            <dd>{mine.calls}</dd>
            <dt>Files</dt>
            <dd>{mine.artifacts}</dd>
          </dl>
        </>
      )}
    </section>
  );
}

export function Studio({ runId, agent }: { runId: string; agent: string }) {
  const state = useQuery({
    queryKey: ["run", runId, "agents"],
    queryFn: () => getJson<RunAgents>(`/api/runs/${runId}/agents`),
    // Events drive updates; this only catches a worker that ends without writing one.
    refetchInterval: (q) => (q.state.data?.busy ? 3000 : false),
  });
  const project = useQuery({
    queryKey: ["run", runId, "project"],
    queryFn: () => getJson<Project>(`/api/runs/${runId}/project`),
  });
  useRunEvents(runId, state.data?.last_event);
  const client = useQueryClient();
  const resume = useMutation({
    mutationFn: () => postJson(`/api/runs/${runId}/resume`, {}),
    onSettled: () => client.invalidateQueries({ queryKey: ["run", runId] }),
  });
  // A run with work left and no worker (the studio restarted mid-step, or it was started
  // elsewhere) carries on when its project is opened; a failed one waits for Retry.
  const stalled =
    state.data !== undefined &&
    ["pending", "running"].includes(state.data.status) &&
    !state.data.busy &&
    !state.data.gate;
  useEffect(() => {
    if (stalled && resume.isIdle) resume.mutate();
  }, [stalled, resume]);
  if (state.error) {
    return (
      <main className="home">
        <p className="error">{state.error.message}</p>
        <a href={href({})} onClick={navClick({})}>
          ← All projects
        </a>
      </main>
    );
  }
  const data = state.data;
  const me = data?.agents.find((a) => a.name === agent);
  return (
    <div className="studio">
      <header className="topbar">
        <a href={href({})} onClick={navClick({})} className="home-link">
          ← Projects
        </a>
        <h1>{project.data?.title ?? "…"}</h1>
        {project.data?.mock && <span className="tag">offline</span>}
        {data?.play && (
          <a className="button" href={data.play.url} target="_blank" rel="noopener">
            ▶ Play v{data.play.version}
          </a>
        )}
      </header>
      {data && <AgentList runId={runId} agents={data.agents} selected={agent} />}
      <main className="center">
        <header className="chat-head">
          <h2>{agent}</h2>
          {me && <span className={`state agent-${me.status}`}>{me.status.replace("_", " ")}</span>}
          {data?.error && <span className="error">{data.error}</span>}
          {data?.status === "failed" && (
            <button type="button" onClick={() => resume.mutate()} disabled={resume.isPending}>
              Retry
            </button>
          )}
          {resume.error && <span className="error">{resume.error.message}</span>}
        </header>
        <Chat key={agent} runId={runId} agent={agent} working={me?.status === "working"} />
      </main>
      <aside className="side">
        {data && <CostPanel state={data} agent={agent} mock={project.data?.mock ?? false} />}
      </aside>
    </div>
  );
}
