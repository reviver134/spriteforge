import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { artifactUrl, getJson, postForm, type Reference } from "./api";

const ART = new Set(["Pixel Artist", "Art Director"]);

/** Who uses a reference and how (stages/references.py). */
export function referenceUse(agent: string): string {
  if (agent === "Director") return "Every agent sees it; the art agents draw new art in its style.";
  if (ART.has(agent)) return `The ${agent} sees it and draws new or redrawn art in its style.`;
  return `The ${agent} sees it with its work.`;
}

/** A chosen image waiting for the rights confirmation, then uploaded for `agent`. */
export function ReferenceUpload({
  runId,
  agent,
  file,
  onDone,
}: {
  runId: string;
  agent: string;
  file: File;
  onDone: () => void;
}) {
  const client = useQueryClient();
  const [rights, setRights] = useState(false);
  const [preview, setPreview] = useState<string>();
  useEffect(() => {
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.append("file", file);
      form.append("agent", agent);
      form.append("rights", String(rights));
      return postForm<Reference>(`/api/runs/${runId}/references`, form);
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["run", runId] });
      onDone();
    },
  });
  return (
    <div className="reference-upload" role="group" aria-label="Add a reference">
      {preview && <img src={preview} alt={file.name} />}
      <div>
        <p>
          <strong>{file.name}</strong> as a reference {agent === "Director" ? "for every agent" : `for the ${agent}`}.
        </p>
        <p className="hint">{referenceUse(agent)}</p>
        <label className="rights">
          <input type="checkbox" checked={rights} onChange={(e) => setRights(e.target.checked)} />
          I have the right to use this image
        </label>
        <div className="actions">
          <button type="button" disabled={!rights || upload.isPending} onClick={() => upload.mutate()}>
            Add reference
          </button>
          <button type="button" className="secondary" onClick={onDone}>
            Cancel
          </button>
        </div>
        {upload.error && <p className="error">{upload.error.message}</p>}
      </div>
    </div>
  );
}

/** The right panel: this agent's references (its own and every agent's) and a drop zone. */
export function ReferencesPanel({ runId, agent }: { runId: string; agent: string }) {
  const refs = useQuery({
    queryKey: ["run", runId, "references"],
    queryFn: () => getJson<{ references: Reference[] }>(`/api/runs/${runId}/references`),
  });
  const [file, setFile] = useState<File | null>(null);
  const [over, setOver] = useState(false);
  const mine = (refs.data?.references ?? []).filter((r) => r.agent === agent || r.everyone).reverse();
  return (
    <section className="panel" aria-label="References">
      <h2>References</h2>
      {mine.length > 0 ? (
        <ul className="references">
          {mine.map((r) => (
            <li key={r.id}>
              <a href={artifactUrl(r.id)} target="_blank" rel="noopener">
                <img src={artifactUrl(r.id)} alt={r.file} />
              </a>
              <span>
                {r.file}
                <small>{r.everyone ? "every agent" : r.agent}</small>
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="muted">No references for the {agent} yet.</p>
      )}
      {file ? (
        <ReferenceUpload runId={runId} agent={agent} file={file} onDone={() => setFile(null)} />
      ) : (
        <label
          className={`drop ${over ? "over" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            const dropped = e.dataTransfer.files[0];
            if (dropped) setFile(dropped);
          }}
        >
          Drop an image here, or choose one
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            aria-label={`Reference image for the ${agent}`}
            onChange={(e) => {
              const chosen = e.target.files?.[0];
              if (chosen) setFile(chosen);
              e.target.value = "";
            }}
          />
        </label>
      )}
    </section>
  );
}
