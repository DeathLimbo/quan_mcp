"""Storage protocols for the strategy-governance domain.

Repositories are the ONLY persistence boundary. The state-mutating method is
``compare_and_set_state`` — there is no plain ``update_state``; callers must
declare the expected ``from_state`` so concurrent promotions lose loudly rather
than silently overwriting each other (issue #10 §5, §12 concurrency test).
"""
from __future__ import annotations

from datetime import date
from typing import Protocol

from packages.strategy_governance.domain import (
    ChangeRequest,
    EvaluationRun,
    FactorVersion,
    ParameterSetVersion,
    PromotionDecision,
    StrategyState,
    StrategyVersion,
)


class ParameterSetRepository(Protocol):
    def get(self, content_hash: str) -> ParameterSetVersion | None: ...
    def save(self, ps: ParameterSetVersion) -> None: ...


class StrategyVersionRepository(Protocol):
    def get(self, strategy_id: str, version: str) -> StrategyVersion | None: ...
    def get_latest(self, strategy_id: str) -> StrategyVersion | None: ...
    def get_production(self, strategy_id: str) -> StrategyVersion | None: ...
    def save(self, version: StrategyVersion) -> None: ...
    def compare_and_set_state(
        self,
        strategy_id: str,
        version: str,
        expected_from: StrategyState,
        to: StrategyState,
        *,
        approved_by: str | None = None,
        approval_id: str | None = None,
    ) -> bool:
        """Atomically move state iff current == expected_from.

        Returns True on success, False if the row's state no longer matches
        ``expected_from`` (concurrent modification). Any other error raises.
        """
    def list_by_strategy(self, strategy_id: str) -> list[StrategyVersion]: ...


class ChangeRequestRepository(Protocol):
    def get(self, request_id: str) -> ChangeRequest | None: ...
    def save(self, req: ChangeRequest) -> None: ...
    def update_status(
        self,
        request_id: str,
        status: str,
        *,
        derived_version: str | None = None,
        decided_by: str | None = None,
        rejection_reason: str | None = None,
    ) -> bool: ...
    def list_by_strategy(self, strategy_id: str) -> list[ChangeRequest]: ...


class EvaluationRunRepository(Protocol):
    def get(self, run_id: str) -> EvaluationRun | None: ...
    def save(self, run: EvaluationRun) -> None: ...
    def list_for_version(self, strategy_id: str,
                         version: str) -> list[EvaluationRun]: ...


class PromotionDecisionRepository(Protocol):
    def save(self, decision: PromotionDecision) -> None: ...
    def list_for_version(self, strategy_id: str,
                         version: str) -> list[PromotionDecision]: ...


class FactorVersionRepository(Protocol):
    def get(self, factor_id: str, version: str) -> FactorVersion | None: ...
    def save(self, factor: FactorVersion) -> None: ...
    def list_by_factor(self, factor_id: str) -> list[FactorVersion]: ...
    def list_available_at(self, as_of: date) -> list[FactorVersion]: ...
    def list_all_active(self) -> list[FactorVersion]: ...
    def retire(self, factor_id: str, version: str) -> bool: ...
