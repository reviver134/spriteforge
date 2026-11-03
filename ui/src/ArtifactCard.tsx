import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { artifactUrl, getJson, type ArtifactMeta } from "./api";
import { Markdown } from "./Markdown";

const IMAGE_PREVIEW = 24; // thumbnails shown before "Show all"

const base = (name: string) => name.replace(/\.[^./]+$/, "");
const short = (name: string) => name.split("/").slice(-2).join("/");

/** What a set of artifacts shows: pictures, documents (Markdown wins over its JSON twin),
 * build reports, and the rest as links. Muse's raw pictures stay behind their cleaned ones. */
function sort(artifacts: ArtifactMeta[]) {
  const docs = new Set(artifacts.filter((a) => a.mime === "text/markdown").map((a) => base(a.name)));
  const images: ArtifactMeta[] = [];
  const markdown: ArtifactMeta[] = [];
  const reports: ArtifactMeta[] = [];
  const other: ArtifactMeta[] = [];
  for (const a of artifacts) {
    if (a.mime.startsWith("image/")) {
      if (!/-raw\.png$/.test(a.name)) images.push(a);
    } else if (a.mime === "text/markdown") markdown.push(a);
    else if (/^game\/v\d+\/report\.json$/.test(a.name)) reports.push(a);
    else if (!(a.mime === "application/json" && docs.has(base(a.name)))) other.push(a);
  }
  return { images, markdown, reports, other };
}

export function ArtifactGroup({ artifacts }: { artifacts: ArtifactMeta[] }) {
  const [all, setAll] = useState(false);
  const { images, markdown, reports, other } = sort(artifacts);
  const shown = all ? images : images.slice(0, IMAGE_PREVIEW);
  return (
    <div className="artifacts">
      {reports.map((a) => (
        <BuildReport key={a.id} artifact={a} />
      ))}
      {images.length > 0 && (
        <div className="image-grid">
          {shown.map((a) => (
            <a key={a.id} href={artifactUrl(a.id)} target="_blank" rel="noopener" title={a.name}>
              <img src={artifactUrl(a.id)} alt={short(a.name)} loading="lazy" />
              <span>{short(a.name)}</span>
            </a>
          ))}
          {images.length > shown.length && (
            <button type="button" className="secondary" onClick={() => setAll(true)}>
              Show all {images.length}
            </button>
          )}
        </div>
      )}
      {markdown.map((a) => (
        <Doc key={a.id} artifact={a} />
      ))}
      {other.length > 0 && (
        <p className="files">
          {other.map((a) => (
            <a key={a.id} href={artifactUrl(a.id)} target="_blank" rel="noopener" download={
              a.mime === "application/json" || a.mime.startsWith("image/") ? undefined : a.name.split("/").pop()
            }>
              {short(a.name)}
            </a>
          ))}
        </p>
      )}
    </div>
  );
}

function Doc({ artifact }: { artifact: ArtifactMeta }) {
  const [open, setOpen] = useState(false);
  const text = useQuery({
    queryKey: ["artifact-text", artifact.id],
    queryFn: async () => (await fetch(artifactUrl(artifact.id))).text(),
    enabled: open,
    staleTime: Infinity, // artifacts never change
  });
  return (
    <details className="doc" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>{short(artifact.name)}</summary>
      {text.data ? <Markdown text={text.data} /> : <p className="muted">Loading…</p>}
    </details>
  );
}

interface Report {
  title?: string;
  version?: number;
  passed?: boolean;
  play?: string;
  sounds?: unknown;
  levels?: { n: number; name: string; cleared: boolean; play?: string }[];
  qa?: { count?: number; failed?: number; passed?: number; export_url?: string };
  model_calls?: { estimated_cost_usd?: number };
}

function BuildReport({ artifact }: { artifact: ArtifactMeta }) {
  const report = useQuery({
    queryKey: ["artifact-json", artifact.id],
    queryFn: () => getJson<Report>(artifactUrl(artifact.id)),
    staleTime: Infinity,
  });
  const r = report.data;
  if (!r) return null;
  const cleared = r.levels?.filter((l) => l.cleared).length;
  return (
    <div className="build-report">
      <div className="build-head">
        <strong>
          {r.title} · build v{r.version}
        </strong>
        <span className={r.passed ? "ok" : "warn"}>
          {r.passed ? "all checks passed" : "some checks failed"}
        </span>
      </div>
      <p className="muted">
        {r.levels && `${cleared}/${r.levels.length} levels cleared by the bot`}
        {r.qa?.count !== undefined && ` · QA ${r.qa.count - (r.qa.failed ?? 0)}/${r.qa.count} pass`}
      </p>
      {r.play && (
        <p className="play-links">
          <a className="button" href={r.play} target="_blank" rel="noopener">
            ▶ Play v{r.version}
          </a>
          {Boolean(r.sounds) && (
            <a href={r.play.replace(/index\.html$/, "sounds.html")} target="_blank" rel="noopener">
              Edit the sounds
            </a>
          )}
        </p>
      )}
      {r.levels && (
        <ul className="levels">
          {r.levels.map((l) => (
            <li key={l.n}>
              {l.cleared ? "✔" : "✖"} Level {l.n}: {l.name}{" "}
              {l.play && (
                <a href={l.play} target="_blank" rel="noopener">
                  play
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
