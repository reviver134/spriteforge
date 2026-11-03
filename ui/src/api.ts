// Typed wrappers over the local FastAPI server. Errors carry FastAPI's `detail` text.

export type RunStatus =
  | "pending"
  | "running"
  | "paused"
  | "completed"
  | "failed"
  | "aborted"
  | "budget_exhausted";

export interface Run {
  id: string;
  status: RunStatus;
  cursor: number;
  config: { goal?: string; workflow?: string; mock?: boolean } & Record<string, unknown>;
}

export interface RunList {
  runs: Run[];
  active: string | null;
  error: string | null;
}

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof data.detail === "string" ? data.detail : response.statusText);
  }
  return data as T;
}
