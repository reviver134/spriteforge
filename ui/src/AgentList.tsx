import type { Agent } from "./api";
import { href, navClick } from "./nav";

const ICON: Record<Agent["status"], string> = {
  waiting: "○",
  working: "●",
  needs_you: "!",
  done: "✓",
};
const LABEL: Record<Agent["status"], string> = {
  waiting: "waiting",
  working: "working",
  needs_you: "needs you",
  done: "done",
};

export function AgentList({
  runId,
  agents,
  selected,
}: {
  runId: string;
  agents: Agent[];
  selected: string;
}) {
  return (
    <nav className="agents" aria-label="Agents">
      <h2>Agents</h2>
      <ul>
        {agents.map((a) => (
          <li key={a.name}>
            <a
              href={href({ project: runId, agent: a.name })}
              onClick={navClick({ project: runId, agent: a.name })}
              className={`agent agent-${a.status} ${a.name === selected ? "selected" : ""}`}
              aria-current={a.name === selected ? "page" : undefined}
              title={a.last ?? undefined}
            >
              <span className="icon" aria-hidden="true" title={LABEL[a.status]}>
                {ICON[a.status]}
              </span>
              <span className="name">{a.name}</span>
              {a.needs_you > 0 && <span className="badge">{a.needs_you}</span>}
              {a.cost > 0 && <span className="agent-cost">${a.cost.toFixed(2)}</span>}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
