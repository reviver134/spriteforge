// One EventSource per open project: every journal event refreshes the agents and threads.
import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

export function useRunEvents(runId: string, after: number | undefined) {
  const client = useQueryClient();
  const ready = after !== undefined;
  useEffect(() => {
    if (!ready) return;
    let timer: number | undefined;
    const refresh = () => {
      // Bursts (an art batch writes dozens of events) become one refetch.
      window.clearTimeout(timer);
      timer = window.setTimeout(() => {
        client.invalidateQueries({ queryKey: ["run", runId] });
      }, 250);
    };
    const source = new EventSource(`/api/runs/${runId}/events?after=${after}`);
    source.onmessage = refresh;
    source.addEventListener("done", () => {
      refresh();
      source.close();
    });
    source.addEventListener("worker-error", () => {
      refresh();
      source.close();
    });
    return () => {
      window.clearTimeout(timer);
      source.close();
    };
    // `after` only seeds the stream; later values must not reopen it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, ready, client]);
}
