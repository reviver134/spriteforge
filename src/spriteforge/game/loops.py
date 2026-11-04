"""Agent loops: an agent tries again while it improves (2026-11-04).

Each loop (Programmer repairs, Muse redraws, QA check fixes, Menu and Level retries, Art
Reviewer rounds) keeps going until a try brings no improvement `patience` times in a row, or the
agent's own allowance is used up (`Budget.reserve` raises BudgetExceeded: budget.max_agent_cost
since the human's last action). Fixed counts (game.max_repairs ...) are optional limits on top.
"""


class Progress:
    """Scores of successive tries (higher is better). `stuck` once `patience` tries in a row
    did not beat the best so far."""

    def __init__(self, patience: int = 2):
        self.patience, self.best, self.stale = patience, None, 0

    def add(self, score) -> bool:
        """Record a try; True when it is the best so far."""
        if self.best is None or score > self.best:
            self.best, self.stale = score, 0
            return True
        self.stale += 1
        return False

    @property
    def stuck(self) -> bool:
        return self.stale >= self.patience


def within(count: int, limit: int | None) -> bool:
    """`count` tries so far are under an optional fixed limit (None: no limit)."""
    return limit is None or count < limit


def patience(models) -> int:
    """game.patience of the models' config (stand-ins in tests may have none: 2)."""
    cfg = getattr(models, "cfg", None)
    return cfg.game.patience if cfg is not None else 2
