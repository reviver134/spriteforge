import { useMutation, useQueryClient } from "@tanstack/react-query";

import { postJson, type GoBack } from "./api";

const DONE: Record<Exclude<GoBack["state"], "open">, string> = {
  going_back: "Stopping: the next model call is refused, then the round starts again.",
  went_back: "Went back: the round started again with your change.",
  queued: "Queued for later: it is applied at the next review.",
  expired: "That work already reached its review: your change waits there.",
};

/** "This stops the Programmer ... and goes back to the Game Designer": Go back (the default,
 * Enter) or Queue for later. */
export function GoBackCard({ runId, card }: { runId: string; card: GoBack }) {
  const client = useQueryClient();
  const answer = useMutation({
    mutationFn: (later: boolean) => postJson(`/api/runs/${runId}/stop`, { change: card.change, later }),
    onSettled: () => client.invalidateQueries({ queryKey: ["run", runId] }),
  });
  if (card.state !== "open") {
    return <p className={`go-back-state ${card.state}`}>{DONE[card.state]}</p>;
  }
  return (
    <div className="go-back" role="group" aria-label="Go back">
      <button type="button" autoFocus disabled={answer.isPending} onClick={() => answer.mutate(false)}>
        Go back
      </button>
      <button type="button" className="secondary" disabled={answer.isPending}
        onClick={() => answer.mutate(true)}>
        Queue for later
      </button>
      {answer.error && <p className="error">{answer.error.message}</p>}
    </div>
  );
}
