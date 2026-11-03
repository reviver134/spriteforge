import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { ArtifactGroup } from "./ArtifactCard";
import { GateCard } from "./GateCard";
import { Markdown } from "./Markdown";
import { agentPath, getJson, postJson, type ArtifactMeta, type ThreadItem } from "./api";

type Row =
  | { kind: "item"; item: ThreadItem }
  | { kind: "artifacts"; id: number; artifacts: ArtifactMeta[] };

/** Consecutive artifacts (an art batch writes dozens) read as one card. */
function rows(items: ThreadItem[]): Row[] {
  const out: Row[] = [];
  for (const item of items) {
    const last = out[out.length - 1];
    if (item.kind === "artifact") {
      if (last?.kind === "artifacts") last.artifacts.push(item.artifact);
      else out.push({ kind: "artifacts", id: item.id, artifacts: [item.artifact] });
    } else out.push({ kind: "item", item });
  }
  return out;
}

const RUN_TEXT: Record<string, string> = {
  "run.created": "Project started",
  "run.completed": "Finished",
  "run.retry_requested": "Retrying",
  "game.built": "A build is ready",
};

function Item({ runId, item }: { runId: string; item: ThreadItem }) {
  switch (item.kind) {
    case "chat":
      return (
        <div className={`bubble ${item.role} ${item.error ? "failed" : ""}`}>
          {item.role === "agent" ? <Markdown text={item.text} /> : <p>{item.text}</p>}
          {item.role === "user" && item.intent && item.intent !== "idea" && (
            <span className="intent">
              {item.intent === "change" ? "change request" : "question"}
            </span>
          )}
        </div>
      );
    case "progress":
      return <p className="progress">{item.text}</p>;
    case "gate":
      return (
        <GateCard
          key={`${item.gate.id}-${item.gate.status}`}
          runId={runId}
          gate={item.gate}
          artifacts={item.artifacts}
        />
      );
    case "gate_response": {
      const verdict =
        item.status === "approved"
          ? "Approved"
          : item.status === "changes_requested"
            ? "Changes requested"
            : item.status;
      return (
        <div className="bubble user">
          <p>{verdict + (item.text ? `: ${item.text}` : "")}</p>
        </div>
      );
    }
    case "run":
      return (
        <p className={`milestone ${item.type === "run.failed" ? "error" : ""}`}>
          {item.type === "run.failed"
            ? `Stopped: ${item.message ?? "an error"}`
            : (RUN_TEXT[item.type] ?? item.type)}
        </p>
      );
    default:
      return null;
  }
}

export function Chat({ runId, agent, working }: { runId: string; agent: string; working: boolean }) {
  const client = useQueryClient();
  const thread = useQuery({
    queryKey: ["run", runId, "thread", agent],
    queryFn: () => getJson<{ items: ThreadItem[] }>(`${agentPath(runId, agent)}/thread`),
  });
  const [text, setText] = useState("");
  const send = useMutation({
    mutationFn: (message: string) =>
      postJson(`${agentPath(runId, agent)}/messages`, { text: message }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["run", runId] }),
  });
  const end = useRef<HTMLDivElement>(null);
  const count = thread.data?.items.length ?? 0;
  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [count, send.isPending, agent]);

  const submit = () => {
    const message = text.trim();
    if (!message || send.isPending) return;
    setText("");
    send.mutate(message);
  };
  return (
    <section className="chat" aria-label={`Chat with ${agent}`}>
      <div className="thread">
        {thread.isPending && <p className="muted">Loading…</p>}
        {thread.data && count === 0 && (
          <p className="muted">
            The {agent} has not started yet. Anything you tell it is kept for when it does.
          </p>
        )}
        {thread.data &&
          rows(thread.data.items).map((row) =>
            row.kind === "artifacts" ? (
              <ArtifactGroup key={`a${row.id}`} artifacts={row.artifacts} />
            ) : (
              <Item key={row.item.id} runId={runId} item={row.item} />
            ),
          )}
        {send.isPending && (
          <>
            <div className="bubble user">
              <p>{send.variables}</p>
            </div>
            <p className="progress typing">{agent} is answering…</p>
          </>
        )}
        {working && !send.isPending && <p className="progress typing">{agent} is working…</p>}
        {send.error && <p className="error">{send.error.message}</p>}
        <div ref={end} />
      </div>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <textarea
          aria-label={`Message ${agent}`}
          placeholder={`Message ${agent}…`}
          value={text}
          rows={2}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <button type="submit" disabled={!text.trim() || send.isPending}>
          Send
        </button>
      </form>
    </section>
  );
}
