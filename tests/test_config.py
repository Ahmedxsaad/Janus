from __future__ import annotations

import pytest

from modelguard.config import (
    ENV_FRESHNESS_SLA_HOURS,
    ENV_MAX_HOPS,
    ConfigError,
    ScanConfig,
)


def test_the_defaults_reach_a_model_three_hops_downstream():
    """Dataset -> dataset -> mlFeature -> mlModel is the shortest path to a model."""
    config = ScanConfig()
    assert config.max_hops == 3
    assert config.freshness_sla_hours == 6.0


def test_an_override_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv(ENV_FRESHNESS_SLA_HOURS, "12.5")
    monkeypatch.setenv(ENV_MAX_HOPS, "5")

    config = ScanConfig.from_env()
    assert config.freshness_sla_hours == 12.5
    assert config.max_hops == 5


def test_an_unset_override_leaves_the_default(monkeypatch):
    monkeypatch.delenv(ENV_FRESHNESS_SLA_HOURS, raising=False)
    assert ScanConfig.from_env().freshness_sla_hours == 6.0


@pytest.mark.parametrize("value", ["not-a-number", "0", "-3"])
def test_an_unusable_override_fails_loudly_instead_of_falling_back(monkeypatch, value: str):
    """A typo in a threshold must not silently restore the default."""
    monkeypatch.setenv(ENV_FRESHNESS_SLA_HOURS, value)
    with pytest.raises(ConfigError, match=ENV_FRESHNESS_SLA_HOURS):
        ScanConfig.from_env()


@pytest.mark.parametrize("value", ["2.5", "0", "-1"])
def test_a_non_positive_or_fractional_hop_cap_is_refused(monkeypatch, value: str):
    monkeypatch.setenv(ENV_MAX_HOPS, value)
    with pytest.raises(ConfigError, match=ENV_MAX_HOPS):
        ScanConfig.from_env()
