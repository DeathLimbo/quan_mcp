"""Deterministic 8-layer risk engine (spec §风险引擎).

Each layer is a pure function ``(ctx) -> RiskDecision``. The engine executes
layers in fixed order and short-circuits on the first REJECT. Every decision
is auditable and reason-coded so alerts and post-mortems are trivial.

The engine trace is wrapped by :func:`packages.risk.proposal.propose` into a
single :class:`RiskProposal` with ``APPROVED / ADJUSTED / REJECTED`` status,
which is the sole legitimate input to portfolio and paper-order layers.
"""
from packages.risk.engine import (
    RiskContext, RiskDecision, RiskEngine, RiskLayer, RiskVerdict,
    default_engine,
)
from packages.risk.proposal import ProposalStatus, RiskProposal, propose

__all__ = [
    "RiskContext", "RiskDecision", "RiskEngine", "RiskLayer", "RiskVerdict",
    "default_engine",
    "ProposalStatus", "RiskProposal", "propose",
]
