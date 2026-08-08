from __future__ import annotations

import hashlib

import pytest

from janus.config import (
    ENV_FRESHNESS_SLA_HOURS,
    ENV_MAX_HOPS,
    SCORING_VERSION,
    ConfigError,
    ScanConfig,
)
from janus.detect.trust_score import _FALLBACK_CAUSES


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


# --------------------------------------------------------------------------
# The scoring contract, and the bump that has to accompany a change to it
# --------------------------------------------------------------------------

#: The scoring contract as it stands at SCORING_VERSION, fingerprinted.
#:
#: The mechanical half of versioning the score. A trust score is comparable only to
#: another score computed the same way, and this project already changed the
#: function under models it had scored without recording it. Changing a
#: weight, a band boundary, or the set of contributing findings makes this test
#: fail; the fix is to bump SCORING_VERSION and paste the new digest here, which
#: is exactly the moment the change becomes deliberate.
EXPECTED_SCORING = (2, "de197119c9d2c544")


def _scoring_fingerprint() -> str:
    """Digest the weights, the band boundaries, and the contributing findings."""
    defaults = ScanConfig()
    contract = "|".join(
        [
            *(
                f"{name}={getattr(defaults, name):g}"
                for name in sorted(vars(ScanConfig).get("__annotations__", {}))
                if name.startswith(("trust_weight_", "trust_band_"))
            ),
            # The finding set, from the deduction names the detector can emit.
            ",".join(sorted(_FALLBACK_CAUSES)),
        ]
    )
    return hashlib.sha256(contract.encode()).hexdigest()[:16]


def test_the_scoring_contract_cannot_change_without_a_version_bump():
    assert (SCORING_VERSION, _scoring_fingerprint()) == EXPECTED_SCORING, (
        "The trust scoring contract changed. Bump SCORING_VERSION in "
        "janus/config.py and update EXPECTED_SCORING here, so the history "
        "records the discontinuity instead of showing it as a regression."
    )
