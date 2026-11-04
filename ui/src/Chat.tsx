import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { GoBackCard } from "./GoBackCard";
import { ReferenceUpload, referenceUse } from "./References";

import { ArtifactGroup } from "./ArtifactCard";
import { GateCard } from "./GateCard";
import { Markdown } from "./Markdown";
import { ReportCard } from "./ReportCard";
import {
  agentPath,
  artifactUrl,
  getJson,
  postJson,
  type ArtifactMeta,
  type QueuedChange,
  type ThreadItem,
} from "./api";

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

// Where a change asked in chat stands (chat_changes.py files and applies it).
const CHANGE_STATUS: Record<string, string> = {
  queued: "change request · queued",
  applied: "change request · applied",
  unplaced: "change request · not filed",
};

function Item({ runId, item, queued }: { runId: string; item: ThreadItem; queued: QueuedChange[] }) {
  switch (item.kind) {
    case "chat":
      return (
        <div className={`bubble ${item.role} ${item.error ? "failed" : ""}`}>
          {item.role === "agent" ? <Markdown text={item.text} /> : <p>{item.text}</p>}
          {item.rewind && <GoBackCard runId={runId} card={item.rewind} />}
          {item.role === "user" && item.intent && item.intent !== "idea" && (
            <span className={`intent ${item.status ?? ""}`}>
              {item.intent === "change"
                ? (CHANGE_STATUS[item.status ?? ""] ?? "change request")
                : "question"}
            </span>
          )}
        </div>
      );
    case "progress":
      return <p className="progress">{item.text}</p>;
    case "report":
      return <ReportCard report={item.report} />;
    case "reference":
      return (
        <div className="bubble user reference">
          <a href={artifactUrl(item.reference.id)} target="_blank" rel="noopener">
            <img src={artifactUrl(item.reference.id)} alt={item.reference.file} />
          </a>
          <p>
            Reference {item.reference.everyone ? "for every agent" : `for the ${item.reference.agent}`}:{" "}
            {item.reference.file}
          </p>
          <span className="intent">{referenceUse(item.reference.agent)}</span>
        </div>
      );
    case "gate":
      return (
        <GateCard
          key={`${item.gate.id}-${item.gate.status}`}
          runId={runId}
          gate={item.gate}
          artifacts={item.artifacts}
          queued={item.gate.status === "pending" ? queued : []}
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
          <p>
            {verdict +
              (item.text ? `: ${item.text}` : "") +
              (item.chat ? ` (${item.chat} from the agent chats)` : "")}
          </p>
        </div>
      );
    }
    case "run":
      return (
        <p className={`milestone ${item.type === "run.failed" ? "error" : ""}`}>
          {item.type === "run.failed"
            ? `Stopped: ${item.message ?? "an error"}`
            : item.type === "round.rewound" || item.type === "run.paused"
              ? (item.message ?? "Went back")
              : (RUN_TEXT[item.type] ?? item.type)}
        </p>
      );
    default:
      return null;
  }
}

// A starter for the QA Playtester's Suggest a check (the classifier files it as a change).
const CHECK_STARTER = "Check that ";

export function Chat({
  runId,
  agent,
  working,
  queued,
}: {
  runId: string;
  agent: string;
  working: boolean;
  queued: QueuedChange[];
}) {
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
  const box = useRef<HTMLTextAreaElement>(null);
  const [attached, setAttached] = useState<File | null>(null);
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
              <Item key={row.item.id} runId={runId} item={row.item} queued={queued} />
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
      {attached && (
        <div className="composer-tools">
          <ReferenceUpload runId={runId} agent={agent} file={attached} onDone={() => setAttached(null)} />
        </div>
      )}
      {agent === "QA Playtester" && (
        <div className="composer-tools">
          <button
            type="button"
            className="chip"
            onClick={() => {
              if (!text.trim()) setText(CHECK_STARTER);
              box.current?.focus();
            }}
          >
            + Suggest a check
          </button>
          <span className="hint">
            I write it as a test of the game and run it on every build from the next one.
          </span>
        </div>
      )}
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label className="attach" title="Add a reference image">
          <span aria-hidden="true">📎</span>
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            aria-label={`Attach a reference image for the ${agent}`}
            onChange={(e) => {
              const chosen = e.target.files?.[0];
              if (chosen) setAttached(chosen);
              e.target.value = "";
            }}
          />
        </label>
        <textarea
          ref={box}
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
