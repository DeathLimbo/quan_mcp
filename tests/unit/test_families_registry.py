"""§108 family registry integrity: every declared family binds to real
trainer symbols, and every trainer resolves. This is the code-level closure
of "6 families are implemented".
"""
from __future__ import annotations

from packages.models.families import (
    BASELINE_REGISTRY, FAMILIES, FamilyId, _resolve, families_for_market,
    make_baseline,
)
from packages.common.instrument_id import Market


def test_families_cover_all_six_family_ids():
    assert set(FAMILIES.keys()) == set(FamilyId)


def test_every_family_declares_at_least_one_trainer_symbol():
    missing = [f.id for f in FAMILIES.values() if not f.trainer_symbols]
    assert not missing, f"families without trainer_symbols: {missing}"


def test_every_family_trainer_symbol_resolves_to_callable():
    for fam in FAMILIES.values():
        for sym in fam.trainer_symbols:
            obj = _resolve(sym)
            assert callable(obj), (
                f"trainer symbol {sym!r} for family {fam.id} not callable"
            )


def test_every_family_baseline_is_registered():
    for fam in FAMILIES.values():
        for b in fam.baselines:
            assert b in BASELINE_REGISTRY, (
                f"family {fam.id} references unknown baseline {b!r}"
            )
            model = make_baseline(b)
            # Baselines must satisfy the Model protocol contract.
            assert hasattr(model, "predict_one")


def test_market_regime_family_is_not_trade_generating():
    r = FAMILIES[FamilyId.MARKET_REGIME]
    assert r.trade_generating is False


def test_cn_and_us_families_are_partitioned_by_market():
    cn = {f.id for f in families_for_market(Market.CN)}
    us = {f.id for f in families_for_market(Market.US)}
    # MARKET_REGIME is tagged Market.CN in the current spec but is really
    # cross-market; the point of this test is that every trade-generating
    # family belongs to exactly one market.
    trade_ids = {f.id for f in FAMILIES.values() if f.trade_generating}
    assert trade_ids.issubset(cn | us)
    assert not (cn & us) - {FamilyId.MARKET_REGIME}, (
        f"trade-generating family shared between markets: {cn & us}"
    )
