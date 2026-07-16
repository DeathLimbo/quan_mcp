"""Read-only MCP tool surface — spec §93 (16 tools).

Each tool is a thin, typed adapter over a single backend use-case. Handlers
never build ad-hoc SQL or expose free-form endpoints — the whole point of the
MCP boundary is that the Agent can only call these named entrypoints.

All handlers return the shape of :func:`packages.common.response.ok` /
:func:`err`. When a required backend dependency is not wired at construction
time, the tool responds with a stable ``NOT_CONFIGURED`` error so callers get
deterministic behaviour in tests.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timezone
from typing import Any, Callable

from packages.common.errors import ErrorCode, QuantError
from packages.common.instrument_id import InstrumentId, parse_instrument_id
from packages.common.response import err, ok
from packages.common.time_utils import ensure_utc
from packages.data_sources.contracts import Bar
from packages.features.featureset import FeatureSet
from packages.inference.service import Forecast, InferenceService, NoForecast
from packages.common.instrument_id import AssetType, Market
from packages.models.registry import InMemoryModelRegistry, ModelState
from packages.portfolio.builder import (
    PortfolioConfig, build_portfolio, build_portfolio_from_scores,
)
from packages.portfolio.target import evaluate_return_target
from packages.reporting.render import render_daily_report, render_risk_trace
from packages.risk.engine import RiskContext, default_engine
from packages.risk.proposal import propose


def err_str(code: str, message: str) -> dict:
    e = QuantError(message)
    try:
        e.code = ErrorCode(code)
    except ValueError:
        e.code = ErrorCode.INTERNAL_ERROR
    return err(e)


# ---- Tool manifest (spec §93.2) --------------------------------------------

TOOL_MANIFEST: list[dict[str, Any]] = [
    {"name": "data_get_status",
     "description": "Return latest_event_time / latest_available_at / quality_status per (market, dataset).",
     "read_only": True, "params": {}},
    {"name": "instrument_resolve",
     "description": "Resolve a code/name into (possibly ambiguous) InstrumentId candidates.",
     "read_only": True, "params": {"query": "string", "market_hint": "string?"}},
    {"name": "market_get_status",
     "description": "Trading-session state for a market (open/closed/pre/post/holiday).",
     "read_only": True, "params": {"market": "string"}},
    {"name": "fund_get_profile",
     "description": "Fund/ETF static facts and latest NAV snapshot.",
     "read_only": True, "params": {"instrument_id": "string"}},
    {"name": "equity_get_profile",
     "description": "Equity static facts, listing info and latest fundamentals.",
     "read_only": True, "params": {"instrument_id": "string"}},
    {"name": "portfolio_get_snapshot",
     "description": "Portfolio positions, cash and valuation as of a datetime.",
     "read_only": True, "params": {"portfolio_id": "string", "as_of": "datetime"}},
    {"name": "portfolio_get_exposures",
     "description": "Exposure breakdown by market / sector / currency.",
     "read_only": True, "params": {"portfolio_id": "string", "as_of": "datetime"}},
    {"name": "model_get_production",
     "description": "Return the current PRODUCTION model for (market, horizon).",
     "read_only": True, "params": {"market": "string", "horizon_days": "int"}},
    {"name": "forecast_run",
     "description": "Score one instrument at as_of. Returns NO_FORECAST on missing input.",
     "read_only": True,
     "params": {"instrument_id": "string", "as_of": "datetime", "horizon_days": "int"}},
    {"name": "screen_run",
     "description": "Score a universe and return the top-k ranked instruments.",
     "read_only": True,
     "params": {"instrument_ids": "string[]", "as_of": "datetime",
                "horizon_days": "int", "top_k": "int"}},
    {"name": "portfolio_create_proposal",
     "description": "Build target weights from a scored universe. No side effects.",
     "read_only": True,
     "params": {"scores": "map<string, number>", "max_name_weight": "number?"}},
    {"name": "return_target_evaluate",
     "description": "Assess whether a requested return target can proceed to recommendation.",
     "read_only": True,
     "params": {"target_return": "number", "horizon_days": "int",
                "asset_type": "string?", "share_class": "string?",
                "allow_high_risk": "bool?"}},
    {"name": "risk_evaluate_proposal",
     "description": "Run the 8-layer risk engine and return a RiskProposal.",
     "read_only": True,
     "params": {"instrument_id": "string", "side": "int", "quantity": "number",
                "ref_price": "number", "proposed_weight": "number"}},
    {"name": "risk_run_scenario",
     "description": "Apply a scenario shock (bps) and re-evaluate risk.",
     "read_only": True,
     "params": {"instrument_id": "string", "side": "int", "quantity": "number",
                "ref_price": "number", "shock_bps": "number"}},
    {"name": "prediction_record",
     "description": "Persist the Agent's *explanation* of a prediction. Cannot alter model output.",
     "read_only": False,
     "params": {"prediction_id": "string", "explanation": "string", "confirmed": "bool"}},
    {"name": "report_get_payload",
     "description": "Return the structured payload + markdown for a daily report.",
     "read_only": True, "params": {"report_id": "string"}},
    {"name": "evaluation_get_summary",
     "description": "Return the four-tier evaluation summary for a (model_id, window).",
     "read_only": True, "params": {"model_id": "string", "window_id": "string"}},
]


BarLookup = Callable[[InstrumentId, date, date], list[Bar]]
InstrumentLookup = Callable[[InstrumentId], dict[str, Any] | None]
InstrumentResolver = Callable[[str, str | None], list[dict[str, Any]]]
MarketStatusLookup = Callable[[str], dict[str, Any] | None]
DataStatusLookup = Callable[[], list[dict[str, Any]]]
PortfolioSnapshot = Callable[[str, datetime], dict[str, Any] | None]
PortfolioExposures = Callable[[str, datetime], dict[str, Any] | None]
ReportLookup = Callable[[str], dict[str, Any] | None]
EvaluationLookup = Callable[[str, str], dict[str, Any] | None]
PredictionRecorder = Callable[[str, str, bool], str]


class ReadTools:
    """Concrete read-only surface. All backend integration happens through
    injected callables so unit tests can wire deterministic stubs.
    """

    def __init__(
        self,
        *,
        registry: InMemoryModelRegistry,
        featureset: FeatureSet,
        bar_lookup: BarLookup,
        instrument_lookup: InstrumentLookup,
        instrument_resolver: InstrumentResolver | None = None,
        market_status: MarketStatusLookup | None = None,
        data_status: DataStatusLookup | None = None,
        portfolio_snapshot: PortfolioSnapshot | None = None,
        portfolio_exposures: PortfolioExposures | None = None,
        report_lookup: ReportLookup | None = None,
        evaluation_lookup: EvaluationLookup | None = None,
        prediction_recorder: PredictionRecorder | None = None,
    ) -> None:
        self._registry = registry
        self._featureset = featureset
        self._inference = InferenceService(registry, featureset)
        self._bar_lookup = bar_lookup
        self._instrument_lookup = instrument_lookup
        self._instrument_resolver = instrument_resolver
        self._market_status = market_status
        self._data_status = data_status
        self._portfolio_snapshot = portfolio_snapshot
        self._portfolio_exposures = portfolio_exposures
        self._report_lookup = report_lookup
        self._evaluation_lookup = evaluation_lookup
        self._prediction_recorder = prediction_recorder
        self._risk = default_engine()

    # ---- 1: data_get_status ------------------------------------------------

    def data_get_status(self) -> dict:
        if self._data_status is None:
            return err_str("DATA_NOT_READY", "data_status backend not wired")
        return ok(self._data_status())

    # ---- 2: instrument_resolve --------------------------------------------

    def instrument_resolve(self, query: str, market_hint: str | None = None) -> dict:
        if self._instrument_resolver is None:
            # Fall back to canonical parse
            try:
                iid = parse_instrument_id(query)
                return ok([{"instrument_id": iid.canonical(),
                            "confidence": 1.0, "reason": "canonical"}])
            except Exception:
                return err_str("UNKNOWN_INSTRUMENT",
                               f"cannot resolve {query!r} (no resolver wired)")
        return ok(self._instrument_resolver(query, market_hint))

    # ---- 3: market_get_status ---------------------------------------------

    def market_get_status(self, market: str) -> dict:
        if self._market_status is None:
            return err_str("DATA_NOT_READY", "market status backend not wired")
        s = self._market_status(market)
        if s is None:
            return err_str("UNKNOWN_INSTRUMENT", f"unknown market {market!r}")
        return ok(s)

    # ---- 4/5: profiles -----------------------------------------------------

    def fund_get_profile(self, instrument_id: str) -> dict:
        return self._profile(instrument_id, expected_asset_type=("FUND", "ETF"))

    def equity_get_profile(self, instrument_id: str) -> dict:
        return self._profile(instrument_id, expected_asset_type=("EQUITY",))

    def _profile(self, instrument_id: str, *, expected_asset_type: tuple[str, ...]) -> dict:
        iid = parse_instrument_id(instrument_id)
        if iid.asset_type.value not in expected_asset_type:
            return err_str("UNSUPPORTED_ASSET",
                           f"{iid.asset_type.value} not in {expected_asset_type}")
        meta = self._instrument_lookup(iid)
        if meta is None:
            return err_str("UNKNOWN_INSTRUMENT",
                           f"instrument {instrument_id} not registered")
        return ok(meta)

    # ---- 6/7: portfolio ---------------------------------------------------

    def portfolio_get_snapshot(self, portfolio_id: str, as_of: datetime) -> dict:
        if self._portfolio_snapshot is None:
            return err_str("DATA_NOT_READY", "portfolio snapshot backend not wired")
        s = self._portfolio_snapshot(portfolio_id, ensure_utc(as_of))
        if s is None:
            return err_str("UNKNOWN_INSTRUMENT", f"portfolio {portfolio_id} not found")
        return ok(s)

    def portfolio_get_exposures(self, portfolio_id: str, as_of: datetime) -> dict:
        if self._portfolio_exposures is None:
            return err_str("DATA_NOT_READY", "portfolio exposures backend not wired")
        e = self._portfolio_exposures(portfolio_id, ensure_utc(as_of))
        if e is None:
            return err_str("UNKNOWN_INSTRUMENT", f"portfolio {portfolio_id} not found")
        return ok(e)

    # ---- 8: model_get_production ------------------------------------------

    def model_get_production(self, market: str, horizon_days: int) -> dict:
        try:
            mkt = Market(market)
        except ValueError:
            return err_str("UNKNOWN_INSTRUMENT", f"unknown market {market!r}")
        prod = self._registry.get_production(mkt, horizon_days)
        if prod is None:
            return err_str("MODEL_NOT_AVAILABLE",
                           f"no PRODUCTION model for {market}/{horizon_days}d")
        return ok({
            "model_id": prod.model_id, "version": prod.version,
            "market": prod.market.value, "horizon_days": prod.horizon_days,
            "feature_set_hash": prod.feature_set_hash,
            "state": prod.state.value,
        })

    # ---- 9: forecast_run --------------------------------------------------

    def forecast_run(self, instrument_id: str, as_of: datetime,
                     horizon_days: int) -> dict:
        iid = parse_instrument_id(instrument_id)
        as_of_utc = ensure_utc(as_of)
        bars = self._bar_lookup(iid, date(1970, 1, 1), as_of_utc.date(),
                                as_of_utc=as_of_utc)
        result = self._inference.score(
            instrument_id=iid, as_of=as_of_utc,
            horizon_days=horizon_days, bars=bars,
        )
        if isinstance(result, Forecast):
            return ok({"kind": "forecast", **_forecast_to_dict(result)})
        assert isinstance(result, NoForecast)
        return ok({"kind": "no_forecast", **_no_forecast_to_dict(result)})

    # ---- 10: screen_run ---------------------------------------------------

    def screen_run(self, instrument_ids: list[str], as_of: datetime,
                   horizon_days: int, top_k: int = 20) -> dict:
        as_of_utc = ensure_utc(as_of)
        ranked: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for id_str in instrument_ids:
            iid = parse_instrument_id(id_str)
            bars = self._bar_lookup(iid, date(1970, 1, 1), as_of_utc.date(),
                                    as_of_utc=as_of_utc)
            r = self._inference.score(instrument_id=iid, as_of=as_of_utc,
                                       horizon_days=horizon_days, bars=bars)
            if isinstance(r, Forecast):
                ranked.append({"instrument_id": iid.canonical(), "score": r.score})
            else:
                skipped.append({"instrument_id": iid.canonical(),
                                 "reason": r.reason.value})
        ranked.sort(key=lambda x: x["score"], reverse=True)
        return ok({"top": ranked[:top_k], "skipped": skipped})

    # ---- 11: portfolio_create_proposal ------------------------------------

    def portfolio_create_proposal(self, scores: dict[str, float],
                                  max_name_weight: float | None = None) -> dict:
        parsed: dict[InstrumentId, float] = {
            parse_instrument_id(k): float(v) for k, v in scores.items()
        }
        cfg = PortfolioConfig(
            max_name_weight=max_name_weight if max_name_weight is not None else 0.10
        )
        target = build_portfolio_from_scores(parsed, cfg)
        return ok({
            "weights": {iid.canonical(): w for iid, w in target.weights.items()},
            "cash": target.cash,
            "gross": target.gross,
        })

    # ---- 12: return_target_evaluate ---------------------------------------

    def return_target_evaluate(self, *, target_return: float, horizon_days: int,
                               asset_type: str | None = None,
                               share_class: str | None = None,
                               allow_high_risk: bool = False) -> dict:
        try:
            parsed_asset = AssetType(asset_type) if asset_type is not None else None
        except ValueError:
            return err_str("UNSUPPORTED_ASSET", f"unsupported asset_type {asset_type!r}")
        try:
            assessment = evaluate_return_target(
                target_return=target_return,
                horizon_days=horizon_days,
                asset_type=parsed_asset,
                share_class=share_class,
                allow_high_risk=allow_high_risk,
            )
        except ValueError as exc:
            return err_str("UNSUPPORTED_HORIZON", str(exc))
        return ok(assessment.to_dict())

    # ---- 13: risk_evaluate_proposal ---------------------------------------

    def risk_evaluate_proposal(self, *, instrument_id: str, side: int,
                               quantity: float, ref_price: float,
                               proposed_weight: float,
                               portfolio_id: str | None = None,
                               **kwargs: Any) -> dict:
        iid = parse_instrument_id(instrument_id)
        # issue #6: when a portfolio is supplied, derive exposure / currency
        # exposure from backend data so the Agent cannot bypass risk by
        # claiming low exposure. Caller kwargs act as test-only overrides
        # (setdefault keeps them only when the backend has no value).
        if portfolio_id is not None and self._portfolio_exposures is not None:
            td = kwargs.get("trade_date") or date.today()
            as_of_utc = datetime.combine(td, datetime.max.time(),
                                         tzinfo=timezone.utc)
            exp = self._portfolio_exposures(portfolio_id, as_of_utc)
            if exp is not None:
                # issue #6: backend exposure is AUTHORITATIVE — it overwrites
                # any caller claim so the Agent cannot bypass risk by claiming
                # low exposure. Tests that need caller-supplied values must
                # omit portfolio_id (see test_risk_caller_kwargs_when_no_portfolio_id).
                kwargs["gross_frac_current"] = exp.get("gross_frac", 0.0)
                per_name = exp.get("per_name_frac", {}) or {}
                kwargs["exposure_frac_current"] = per_name.get(iid.canonical(), 0.0)
                # currency exposure needs the instrument's local currency
                instr = self._instrument_lookup(iid) if self._instrument_lookup else None
                ccy = instr.get("currency") if isinstance(instr, dict) else None
                if ccy:
                    per_ccy = exp.get("per_ccy_frac", {}) or {}
                    kwargs["per_ccy_exposure_frac_current"] = per_ccy.get(ccy, 0.0)
        ctx = _build_ctx(iid, side, quantity, ref_price, kwargs)
        p = propose(ctx, proposed_weight=proposed_weight)
        return ok({
            "status": p.status.value,
            "approved_weight": p.approved_weight,
            "reasons": list(p.reasons),
            "policy_version": p.policy_version,
            "safe_mode": p.safe_mode,
            "markdown": render_risk_trace(p.trace),
        })

    # ---- 14: risk_run_scenario --------------------------------------------

    def risk_run_scenario(self, *, instrument_id: str, side: int,
                          quantity: float, ref_price: float,
                          shock_bps: float, **kwargs: Any) -> dict:
        iid = parse_instrument_id(instrument_id)
        kwargs = dict(kwargs)
        kwargs["stress_shock_bps"] = shock_bps
        ctx = _build_ctx(iid, side, quantity, ref_price, kwargs)
        trace = self._risk.evaluate(ctx)
        verdict = self._risk.final_verdict(trace)
        return ok({
            "verdict": verdict.value,
            "shock_bps": shock_bps,
            "trace": [asdict(d) | {"verdict": d.verdict.value} for d in trace],
        })

    # ---- 15: prediction_record --------------------------------------------

    def prediction_record(self, prediction_id: str, explanation: str,
                          confirmed: bool) -> dict:
        if self._prediction_recorder is None:
            return err_str("DATA_NOT_READY", "prediction recorder not wired")
        rec_id = self._prediction_recorder(prediction_id, explanation, confirmed)
        return ok({"record_id": rec_id})

    # ---- 16: report_get_payload -------------------------------------------

    def report_get_payload(self, report_id: str) -> dict:
        if self._report_lookup is None:
            return err_str("DATA_NOT_READY", "report backend not wired")
        payload = self._report_lookup(report_id)
        if payload is None:
            return err_str("UNKNOWN_INSTRUMENT", f"report {report_id} not found")
        markdown = payload.get("markdown") or _quick_report_markdown(payload)
        return ok({"payload": payload, "markdown": markdown})

    # ---- 17: evaluation_get_summary ---------------------------------------

    def evaluation_get_summary(self, model_id: str, window_id: str) -> dict:
        if self._evaluation_lookup is None:
            return err_str("DATA_NOT_READY", "evaluation backend not wired")
        s = self._evaluation_lookup(model_id, window_id)
        if s is None:
            return err_str("UNKNOWN_INSTRUMENT",
                           f"no evaluation for {model_id}/{window_id}")
        return ok(s)


# ---- helpers ---------------------------------------------------------------

def _build_ctx(iid: InstrumentId, side: int, quantity: float, ref_price: float,
               kwargs: dict[str, Any]) -> RiskContext:
    return RiskContext(
        instrument_id=iid,
        trade_date=kwargs.get("trade_date") or date.today(),
        side=side, quantity=quantity, ref_price=ref_price,
        # issue #4: never grant trade permission by default. Missing
        # permissions → empty frozenset → _l1_permission REJECTs PERM_DENIED.
        user_permissions=frozenset(kwargs.get("user_permissions") or []),
        prev_close=kwargs.get("prev_close"),
        avg_volume_20d=kwargs.get("avg_volume_20d"),
        exposure_frac_current=kwargs.get("exposure_frac_current", 0.0),
        exposure_frac_limit=kwargs.get("exposure_frac_limit", 0.20),
        gross_frac_current=kwargs.get("gross_frac_current", 0.0),
        gross_frac_limit=kwargs.get("gross_frac_limit", 1.0),
        # issue #6: per-currency cap (§29). When a portfolio_id is supplied
        # the server derives this from backend exposure (see risk_evaluate_proposal);
        # caller kwargs act as test-only override.
        per_ccy_exposure_frac_current=kwargs.get("per_ccy_exposure_frac_current", 0.0),
        per_ccy_exposure_limit=kwargs.get("per_ccy_exposure_limit", 0.40),
        stress_shock_bps=kwargs.get("stress_shock_bps", 0.0),
        stress_shock_limit_bps=kwargs.get("stress_shock_limit_bps", 3000.0),
    )


def _bar_to_dict(b: Bar) -> dict[str, Any]:
    return {
        "date": b.market_local_date.isoformat(),
        "open": float(b.open), "high": float(b.high), "low": float(b.low),
        "close": float(b.close), "volume": float(b.volume),
        "available_at_utc": b.available_at_utc.astimezone(timezone.utc).isoformat(),
    }


def _forecast_to_dict(f: Forecast) -> dict[str, Any]:
    return {
        "instrument_id": f.instrument_id.canonical(),
        "as_of": f.as_of.isoformat(),
        "horizon_days": f.horizon_days,
        "score": f.score,
        "model": f"{f.model_id}@{f.model_version}",
        "feature_hash": f.feature_hash,
    }


def _no_forecast_to_dict(nf: NoForecast) -> dict[str, Any]:
    return {
        "instrument_id": nf.instrument_id.canonical(),
        "as_of": nf.as_of.isoformat(),
        "reason": nf.reason.value,
        "detail": nf.detail,
    }


def _quick_report_markdown(payload: dict[str, Any]) -> str:
    """Fallback renderer for raw report payloads that lack pre-rendered markdown.

    The full :func:`render_daily_report` requires the runtime Forecast objects;
    this helper works on plain dict payloads returned by the backend.
    """
    lines: list[str] = []
    if "date" in payload:
        lines.append(f"# Daily Report — {payload['date']}")
    forecasts = payload.get("forecasts") or []
    if forecasts:
        lines.append("")
        lines.append("## Forecasts")
        lines.append("| Instrument | Score | Horizon |")
        lines.append("|---|---|---|")
        for f in forecasts:
            lines.append(
                f"| {f.get('instrument', '?')} | {f.get('score', 0):+.4f} "
                f"| {f.get('horizon_days', '?')}d |"
            )
    return "\n".join(lines) + "\n"
