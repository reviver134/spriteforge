import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { ArtifactGroup } from "./ArtifactCard";
import { postJson, type ArtifactMeta, type FieldRule, type Gate, type QueuedChange } from "./api";

// Stages of the gated workflow where Request changes is accepted (as the classic view).
const CHANGE_STAGES = new Set(["concept", "assets", "gameplay", "design", "menus", "playtest", "final"]);

const STATUS_TEXT: Record<Gate["status"], string> = {
  pending: "Waiting for you",
  approved: "Approved",
  changes_requested: "Changes requested",
  aborted: "Stopped",
  superseded: "Replaced by a newer version",
};

type Values = Record<string, string | boolean>;

function initial(gate: Gate): Values {
  const values: Values = {};
  for (const [name, rule] of Object.entries(fields(gate))) {
    const saved = gate.response?.form?.[name];
    const value = saved ?? rule.default ?? (rule.type === "boolean" ? false : "");
    values[name] = typeof value === "boolean" ? value : String(value);
  }
  return values;
}

function fields(gate: Gate): Record<string, FieldRule> {
  // A plain "Feedback" schema has no fields of its own: the text box is the answer.
  return gate.form_schema.title === "Feedback" ? {} : gate.form_schema.properties ?? {};
}

function Field({
  name,
  rule,
  value,
  disabled,
  onChange,
}: {
  name: string;
  rule: FieldRule;
  value: string | boolean;
  disabled: boolean;
  onChange: (v: string | boolean) => void;
}) {
  const label = rule.title || name;
  let input;
  if (rule.enum) {
    input = (
      <select aria-label={label} value={String(value)} disabled={disabled} onChange={(e) => onChange(e.target.value)}>
        {rule.enum.map((v) => (
          <option key={v}>{v}</option>
        ))}
      </select>
    );
  } else if (rule.type === "boolean") {
    input = (
      <input type="checkbox" aria-label={label} checked={Boolean(value)} disabled={disabled}
        onChange={(e) => onChange(e.target.checked)} />
    );
  } else {
    const type = rule.format === "color" ? "color" : ["integer", "number"].includes(rule.type ?? "") ? "number" : "text";
    input = (
      <input type={type} aria-label={label} value={String(value)} disabled={disabled}
        onChange={(e) => onChange(e.target.value)} />
    );
  }
  return (
    <div className="field">
      <label>
        <span>{label}</span>
        {input}
      </label>
      {rule.description && <p className="hint">{rule.description}</p>}
      {rule.suggestions && rule.format !== "color" && !disabled && (
        <div className="chips">
          {rule.suggestions.map((s) => (
            <button key={s} type="button" className={value === s ? "chip on" : "chip"} onClick={() => onChange(s)}>
              {s}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

/** "G4 Playtest v2: Steel River Strike, ..." -> ["G4 Playtest v2", "Steel River Strike, ..."]:
 * the first ": " or ". " outside parentheses ends the heading. */
function splitTitle(title: string): [string, string] {
  let depth = 0;
  for (let i = 0; i < title.length - 1; i++) {
    const c = title[i];
    if (c === "(") depth++;
    else if (c === ")") depth = Math.max(0, depth - 1);
    else if (depth === 0 && (c === ":" || c === ".") && title[i + 1] === " ") {
      return [title.slice(0, i), title.slice(i + 2)];
    }
  }
  return [title, ""];
}

export function GateCard({
  runId,
  gate,
  artifacts,
  queued = [],
}: {
  runId: string;
  gate: Gate;
  artifacts: ArtifactMeta[];
  queued?: QueuedChange[];
}) {
  const client = useQueryClient();
  const [values, setValues] = useState<Values>(() => initial(gate));
  const [text, setText] = useState(gate.response?.text ?? "");
  const open = gate.status === "pending";
  const answer = useMutation({
    mutationFn: (action: string) => {
      const form: Record<string, unknown> = {};
      for (const [name, rule] of Object.entries(fields(gate))) {
        const v = values[name];
        form[name] = ["integer", "number"].includes(rule.type ?? "") ? Number(v) : v;
      }
      return postJson(`/api/runs/${runId}/gates/${gate.id}?wait=false`, { action, text, form });
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ["run", runId] }),
  });
  const single = gate.empty_feedback_approves;
  // The Director's plan for G4/G5 feedback: approve it before anything is built, or correct it.
  const plan = gate.stage === "confirm";
  const canChange = gate.kind !== "confirm_changes" && CHANGE_STAGES.has(gate.stage);
  const busy = answer.isPending;
  const [heading, rest] = splitTitle(gate.title);
  // Changes asked in agent chats are applied by Request changes, with or without more text.
  const fromChat = open && canChange ? queued : [];
  const changeLabel =
    fromChat.length && !text.trim()
      ? `Build with ${fromChat.length} change${fromChat.length > 1 ? "s" : ""}`
      : "Request changes";
  return (
    <section className={`gate-card ${open ? "open" : ""}`} aria-label={heading}>
      <header>
        <h3>{heading}</h3>
        <span className={`gate-status gate-${gate.status}`}>{STATUS_TEXT[gate.status]}</span>
      </header>
      {rest && <p className="gate-text">{rest}</p>}
      {artifacts.length > 0 && <ArtifactGroup artifacts={artifacts} />}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          const submitter = (e.nativeEvent as SubmitEvent).submitter as HTMLButtonElement | null;
          answer.mutate(submitter?.value ?? (single ? "submit" : "approve"));
        }}
      >
        {Object.entries(fields(gate)).map(([name, rule]) => (
          <Field key={name} name={name} rule={rule} value={values[name]} disabled={!open || busy}
            onChange={(v) => setValues((old) => ({ ...old, [name]: v }))} />
        ))}
        {fromChat.length > 0 && (
          <section className="queued" aria-label="Changes from the chats">
            <p className="hint">From the agent chats, applied with {changeLabel}:</p>
            <ul>
              {fromChat.map((c) => (
                <li key={c.id}>
                  <strong>{c.agent}:</strong> {c.text}
                </li>
              ))}
            </ul>
          </section>
        )}
        {(open || text) && (
          <textarea
            aria-label="Feedback"
            placeholder={
              plan
                ? "Type what the Director got wrong, or leave empty to approve the plan"
                : single
                  ? "Type what to change, or leave empty to approve"
                  : "Feedback (optional)"
            }
            value={text}
            rows={2}
            disabled={!open || busy}
            onChange={(e) => setText(e.target.value)}
          />
        )}
        {open && (
          <div className="actions">
            {single ? (
              <>
                {fromChat.length > 0 && !text.trim() && (
                  <button type="submit" value="request_changes" className="secondary" disabled={busy}>
                    {changeLabel}
                  </button>
                )}
                <button type="submit" value="submit" disabled={busy}>
                  {text.trim()
                    ? plan ? "Correct the plan" : "Send feedback (new version)"
                    : plan ? "Approve the plan" : "Approve"}
                </button>
              </>
            ) : (
              <>
                {canChange && (
                  <button
                    type="submit"
                    value="request_changes"
                    className="secondary"
                    disabled={busy || (!text.trim() && !fromChat.length)}
                  >
                    {changeLabel}
                  </button>
                )}
                <button type="submit" value="approve" disabled={busy}>
                  Approve
                </button>
              </>
            )}
          </div>
        )}
        {answer.error && <p className="error">{answer.error.message}</p>}
      </form>
    </section>
  );
}
