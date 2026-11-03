// Typed wrappers over the local FastAPI server. Errors carry FastAPI's `detail` text.

export type RunStatus =
  | "pending"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "aborted"
  | "budget_exhausted";

export interface Project {
  id: string;
  title: string;
  idea: string;
  status: RunStatus;
  workflow: string;
  mock: boolean;
  created: string | null;
  cost: number;
  thumbnail: string | null;
  gate: { id: string; title: string; stage: string } | null;
  busy: boolean;
}

export interface Setup {
  meta_api_key: boolean;
  chromium: boolean;
}

async function parse<T>(response: Response): Promise<T> {
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : response.statusText);
  }
  return data as T;
}

export async function getJson<T>(path: string): Promise<T> {
  return parse<T>(await fetch(path));
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  return parse<T>(
    await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  );
}
