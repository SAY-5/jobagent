from __future__ import annotations

from jobagent.retry import BackoffStrategy, RetryPolicy, RetryRegistry


def test_first_attempt_has_zero_delay() -> None:
    p = RetryPolicy(base_seconds=2.0)
    assert p.delay_for(1) == 0.0


def test_constant_backoff() -> None:
    p = RetryPolicy(base_seconds=2.0, strategy=BackoffStrategy.CONSTANT)
    assert p.delay_for(2) == 2.0
    assert p.delay_for(5) == 2.0


def test_linear_backoff_grows_linearly() -> None:
    p = RetryPolicy(base_seconds=1.0, strategy=BackoffStrategy.LINEAR)
    assert p.delay_for(2) == 1.0
    assert p.delay_for(3) == 2.0
    assert p.delay_for(4) == 3.0


def test_exponential_backoff_doubles() -> None:
    p = RetryPolicy(base_seconds=1.0, strategy=BackoffStrategy.EXPONENTIAL)
    assert p.delay_for(2) == 1.0
    assert p.delay_for(3) == 2.0
    assert p.delay_for(4) == 4.0
    assert p.delay_for(5) == 8.0


def test_max_seconds_caps_delay() -> None:
    p = RetryPolicy(base_seconds=1.0, strategy=BackoffStrategy.EXPONENTIAL, max_seconds=5.0)
    assert p.delay_for(10) == 5.0


def test_should_retry_respects_max_attempts() -> None:
    p = RetryPolicy(max_attempts=3)
    assert p.should_retry(1)
    assert p.should_retry(2)
    assert not p.should_retry(3)


def test_registry_falls_back_to_default() -> None:
    r = RetryRegistry()
    assert r.policy_for("never-registered").max_attempts == 3


def test_registry_returns_registered_policy() -> None:
    fast = RetryPolicy(max_attempts=10, base_seconds=0.1)
    r = RetryRegistry()
    r.register("network-probe", fast)
    assert r.policy_for("network-probe").max_attempts == 10
