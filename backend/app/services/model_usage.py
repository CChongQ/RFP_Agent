from dataclasses import dataclass


@dataclass(frozen=True)
class ModelUsageSnapshot:
    """Stores cumulative model usage at one point in time"""

    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float


class ModelUsageTracker:
    """Accumulates usage reported by model responses"""

    def __init__(self) -> None:
        self._input_tokens = 0
        self._output_tokens = 0
        self._estimated_cost_usd = 0.0

    def add(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        estimated_cost_usd: float = 0.0,
    ) -> None:
        # One shared counter combines extraction and decision model calls
        if input_tokens < 0 or output_tokens < 0 or estimated_cost_usd < 0:
            raise ValueError("model usage values cannot be negative")
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens
        self._estimated_cost_usd += estimated_cost_usd

    def snapshot(self) -> ModelUsageSnapshot:
        return ModelUsageSnapshot(
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            estimated_cost_usd=self._estimated_cost_usd,
        )


def usage_since(
    before: ModelUsageSnapshot,
    after: ModelUsageSnapshot,
) -> ModelUsageSnapshot:
    """Return usage added between two cumulative snapshots"""

    # Subtract snapshots so a reused tracker reports only the current run
    return ModelUsageSnapshot(
        input_tokens=after.input_tokens - before.input_tokens,
        output_tokens=after.output_tokens - before.output_tokens,
        estimated_cost_usd=after.estimated_cost_usd - before.estimated_cost_usd,
    )
