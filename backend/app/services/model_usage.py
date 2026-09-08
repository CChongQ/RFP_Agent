from dataclasses import dataclass


@dataclass(frozen=True)
class ModelUsageSnapshot:
    """Stores cumulative model usage at one point in time"""

    input_tokens: int
    output_tokens: int
    unreported_calls: int = 0


class ModelUsageTracker:
    """Accumulates usage reported by model responses"""

    def __init__(
        self,
        *,
        input_cost_per_mil_tokens: float | None = None,
        output_cost_per_mil_tokens: float | None = None,
    ) -> None:
        self._input_tokens = 0
        self._output_tokens = 0
        self._unreported_calls = 0
        self._input_price = input_cost_per_mil_tokens
        self._output_price = output_cost_per_mil_tokens

    def add(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        # One shared counter combines extraction and decision model calls
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("model usage values cannot be negative")
        self._input_tokens += input_tokens
        self._output_tokens += output_tokens

    def record_missing_usage(self) -> None:
        """Mark returned response whose token usage was unavailable"""

        self._unreported_calls += 1

    def snapshot(self) -> ModelUsageSnapshot:
        return ModelUsageSnapshot(
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            unreported_calls=self._unreported_calls,
        )

    def estimate_cost(self, usage: ModelUsageSnapshot) -> float | None:
        """Estimate reported generation usage at the configured standard rates"""

        if (
            self._input_price is None
            or self._output_price is None
            or usage.unreported_calls > 0
        ):
            return None
        return (
            usage.input_tokens * self._input_price
            + usage.output_tokens * self._output_price
        ) / 1_000_000


def usage_since(
    before: ModelUsageSnapshot,
    after: ModelUsageSnapshot,
) -> ModelUsageSnapshot:
    """Return usage added between two cumulative snapshots"""

    # Subtract snapshots so a reused tracker reports only the current run
    return ModelUsageSnapshot(
        input_tokens=after.input_tokens - before.input_tokens,
        output_tokens=after.output_tokens - before.output_tokens,
        unreported_calls=after.unreported_calls - before.unreported_calls,
    )
