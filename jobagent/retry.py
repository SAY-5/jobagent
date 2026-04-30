"""v4: per-class retry policies.

The orchestrator (v1) handles every job the same way. v4 adds
RetryPolicy: per-job-class config for max attempts + backoff
strategy. Some jobs (network probes) want fast retries; some
(LLM calls) want slow exponential backoff to avoid burning
budget on a flapping API.

The policy is a value object; the orchestrator looks up the
right one by job class on each retry decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class BackoffStrategy(StrEnum):
    CONSTANT = "constant"
    LINEAR = "linear"
    EXPONENTIAL = "exponential"


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 3
    base_seconds: float = 1.0
    strategy: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    max_seconds: float = 60.0

    def delay_for(self, attempt: int) -> float:
        """Delay BEFORE the n-th attempt (attempt is 1-indexed; the
        delay before attempt 1 is 0 because we always try once)."""
        if attempt <= 1:
            return 0.0
        n = attempt - 1
        if self.strategy == BackoffStrategy.CONSTANT:
            d = self.base_seconds
        elif self.strategy == BackoffStrategy.LINEAR:
            d = self.base_seconds * n
        else:
            d = self.base_seconds * (2 ** (n - 1))
        return min(d, self.max_seconds)

    def should_retry(self, attempt: int) -> bool:
        return attempt < self.max_attempts


@dataclass
class RetryRegistry:
    """Maps job class → policy. Falls back to a default for
    unregistered classes."""

    default: RetryPolicy = field(default_factory=RetryPolicy)
    _by_class: dict[str, RetryPolicy] = field(default_factory=dict)

    def register(self, job_class: str, policy: RetryPolicy) -> None:
        self._by_class[job_class] = policy

    def policy_for(self, job_class: str) -> RetryPolicy:
        return self._by_class.get(job_class, self.default)
