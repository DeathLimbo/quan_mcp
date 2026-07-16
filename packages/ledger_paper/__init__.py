"""Paper-trading double-entry ledger (spec §87).

All monetary values are ``Decimal``. Every transaction posts equal debit and
credit legs, so ``sum(cash + Σ position * mark) == portfolio_value`` is an
identity by construction, verifiable at any time via :func:`Portfolio.balance`.

Objects here are intentionally minimal — only what is needed to model paper
fills without a database. A production wiring persists rows into
``portfolio / account / cash_balance / position_lot / order_intent /
paper_order / paper_fill / portfolio_valuation / pnl_attribution``.
"""
from packages.ledger_paper.ledger import (
    Account, AccountType, Currency, JournalEntry, Leg, OrderIntent,
    PaperFill, Portfolio, Position, Trade, PortfolioValuation,
)
from packages.ledger_paper.simulator import (
    SimulatedBroker, ReconciliationRow, reconcile_forecast_vs_fills,
)

__all__ = [
    "Account", "AccountType", "Currency", "JournalEntry", "Leg",
    "OrderIntent", "PaperFill", "Portfolio", "Position", "Trade",
    "PortfolioValuation",
    "SimulatedBroker", "ReconciliationRow", "reconcile_forecast_vs_fills",
]
