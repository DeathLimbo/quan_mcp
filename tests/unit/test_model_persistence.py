"""Tests for model persistence + regression return prediction.

Covers:
- TrainedLightGBMModel.predict_return (regression returns raw, classification None)
- TrainedLightGBMModel.save/load round-trip (identical predictions)
- SqlModelRegistry register/transition/get_production/get_latest_production
  with artifact reload (survives restart)
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

import sqlalchemy as sa

from packages.common.instrument_id import Market
from packages.common.time_utils import utcnow
from packages.datasets.builder import DatasetRow
from packages.models.registry import ModelRecord, ModelState
from packages.persistence.repositories import SqlModelRegistry, _metadata, model_registry_t
from packages.training import LightGBMTrainer
from packages.training.lightgbm_trainer import TrainedLightGBMModel

FEATURES = ["ret_1d", "ret_5d", "vol_20d"]


def _rows(n: int = 200) -> list[DatasetRow]:
    rng = random.Random(42)
    return [
        DatasetRow(
            as_of_date=date(2024, 1, 1) + timedelta(days=i),
            features={f: rng.gauss(0, 0.05) for f in FEATURES},
            label=rng.gauss(0, 0.04),
            feature_set_hash="testhash",
        )
        for i in range(n)
    ]


def test_regression_predict_return_is_raw_float():
    rows = _rows()
    m = LightGBMTrainer(FEATURES, 20, task="regression", num_boost_round=10).fit(
        rows, model_id="reg")
    feats = rows[-1].features
    ret = m.predict_return(feats)
    assert ret is not None
    assert isinstance(ret, float)
    # predict_one still squashes to [0,1] for the inference-service contract
    assert 0.0 <= m.predict_one(feats).score <= 1.0


def test_classifier_predict_return_is_none():
    rows = _rows()
    m = LightGBMTrainer(FEATURES, 20, task="classification", num_boost_round=10).fit(
        rows, model_id="clf")
    assert m.predict_return(rows[-1].features) is None


def test_model_save_load_roundtrip_identical(tmp_path: Path):
    rows = _rows()
    m = LightGBMTrainer(FEATURES, 20, task="regression", num_boost_round=10).fit(
        rows, model_id="rt")
    feats = rows[-1].features
    before = m.predict_return(feats)
    path = str(tmp_path / "rt")
    m.save(path)
    assert Path(path + ".lgb").exists()
    assert Path(path + ".json").exists()
    m2 = TrainedLightGBMModel.load(path)
    after = m2.predict_return(feats)
    assert abs(before - after) < 1e-9
    assert m2.task == "regression"
    assert m2.feature_names == m.feature_names
    assert m2.model_id == m.model_id


def test_sql_model_registry_persists_and_reloads_artifact(tmp_path: Path):
    engine = sa.create_engine("sqlite://")
    _metadata.create_all(engine, tables=[model_registry_t])
    reg = SqlModelRegistry(engine, str(tmp_path))

    rows = _rows()
    m = LightGBMTrainer(FEATURES, 20, task="regression", num_boost_round=10).fit(
        rows, model_id="sqltest")
    rec = ModelRecord(
        model_id="sqltest", version=m.version, market=Market.CN,
        horizon_days=20, feature_set_hash=m.feature_set_hash,
        state=ModelState.DRAFT, created_at=utcnow(),
        approved_by=None, approval_id=None, metrics={}, notes=None)
    reg.register(rec, artifact=m, metrics={"train_rows": 200.0})

    got = reg.get("sqltest", m.version)
    assert got is not None
    assert got.state is ModelState.DRAFT
    assert got.market is Market.CN
    assert got.metrics.get("train_rows") == 200.0

    reg.transition("sqltest", m.version, ModelState.PRODUCTION,
                   actor="tester", approval_id="apr1", metrics={"ic": 0.12})

    prod = reg.get_production(Market.CN, 20)
    assert prod is not None
    assert prod.state is ModelState.PRODUCTION
    assert prod.approval_id == "apr1"

    rec2, art = reg.get_latest_production("sqltest")
    assert rec2 is not None
    assert art is not None
    feats = rows[-1].features
    assert abs(art.predict_return(feats) - m.predict_return(feats)) < 1e-9


def test_sql_model_registry_latest_production_distinguishes_model_ids(tmp_path: Path):
    """Two PRODUCTION models under the same (market, horizon) but different
    model_ids must be retrievable independently via get_latest_production."""
    engine = sa.create_engine("sqlite://")
    _metadata.create_all(engine, tables=[model_registry_t])
    reg = SqlModelRegistry(engine, str(tmp_path))
    rows = _rows()

    for mid in ("cn_equity_reg20d", "fund_nav_reg20d"):
        m = LightGBMTrainer(FEATURES, 20, task="regression",
                            num_boost_round=10).fit(rows, model_id=mid)
        rec = ModelRecord(model_id=mid, version=m.version, market=Market.CN,
                          horizon_days=20, feature_set_hash=m.feature_set_hash,
                          state=ModelState.DRAFT, created_at=utcnow(),
                          approved_by=None, approval_id=None, metrics={},
                          notes=None)
        reg.register(rec, artifact=m)
        reg.transition(mid, m.version, ModelState.PRODUCTION,
                       actor="t", approval_id="a")

    # get_production(market,horizon) returns just the newest — ambiguous for
    # two same-market models. get_latest_production(model_id) disambiguates:
    r1, a1 = reg.get_latest_production("cn_equity_reg20d")
    r2, a2 = reg.get_latest_production("fund_nav_reg20d")
    assert r1 is not None and a1 is not None
    assert r2 is not None and a2 is not None
    assert r1.model_id == "cn_equity_reg20d"
    assert r2.model_id == "fund_nav_reg20d"
