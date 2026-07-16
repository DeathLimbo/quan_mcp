"""FX converter core (spec §3.2 FX Adapter + §12.6 cross-currency attribution).

Converts amounts between currencies using point-in-time rates and attributes
realised FX return. The converter is deliberately agnostic to the storage
layer — it takes a ``rate_provider`` callable (signature mirrors
``SqlFxRepository.get_as_of``) so it can be wired to Postgres in production
and to an in-memory dict in tests.

Conservative on purpose: ``fx_return`` uses *realised* history only. Spec §12.6
forbids packaging FX point forecasts as high-confidence conclusions unless a
separate FX model passes validation — we therefore never emit a forecasted
FX number, only the historical realised contribution.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Callable, Optional

from packages.common.errors import QuantError


class FxNotAvailableError(QuantError):
    """No FX rate on or before the requested date — cannot convert safely."""


# (base, quote, on_or_before) -> rate | None. Mirrors SqlFxRepository.get_as_of
# (which accepts an optional as_of_utc we deliberately leave defaulted here).
RateProvider = Callable[[str, str, date], Optional[Decimal]]


@dataclass(frozen=True, slots=True)
class FxConverter:
    """Point-in-time currency converter.

    ``base_ccy`` is the portfolio base currency (user-configurable; spec §29).
    All cross-currency positions are reported in both local and base currency.
    """

    base_ccy: str = "CNY"
    rate_provider: Optional[RateProvider] = None

    # -- conversions -------------------------------------------------------

    def convert(
        self,
        amount: Decimal,
        *,
        from_ccy: str,
        to_ccy: str,
        on_or_before: date,
    ) -> Decimal:
        """Convert ``amount`` from ``from_ccy`` to ``to_ccy`` as of ``on_or_before``."""
        if from_ccy == to_ccy:
            return amount
        if self.rate_provider is None:
            raise FxNotAvailableError("no rate_provider configured")
        rate = self._lookup(from_ccy, to_ccy, on_or_before)
        if rate is None:
            raise FxNotAvailableError(
                f"no FX rate {from_ccy}/{to_ccy} on or before {on_or_before}"
            )
        return amount * rate

    def to_base(
        self, amount: Decimal, *, from_ccy: str, on_or_before: date,
    ) -> Decimal:
        """Convert any currency amount into the portfolio base currency (§29)."""
        return self.convert(
            amount, from_ccy=from_ccy, to_ccy=self.base_ccy,
            on_or_before=on_or_before,
        )

    # -- attribution -------------------------------------------------------

    def fx_return(
        self, *, local_ccy: str, start: date, end: date,
    ) -> Decimal:
        """Realised FX return of ``local_ccy`` vs base over [start, end] (§12.6).

        ``(rate_end - rate_start) / rate_start`` for the local→base pair. For a
        local-currency asset this is the FX contribution to add to the local
        return to approximate the base-currency return. Conservative: realised
        history only, never a forecast.
        """
        if local_ccy == self.base_ccy:
            return Decimal("0")
        r0 = self._lookup(local_ccy, self.base_ccy, start)
        r1 = self._lookup(local_ccy, self.base_ccy, end)
        if r0 is None or r1 is None or r0 == 0:
            raise FxNotAvailableError(
                f"cannot compute fx_return {local_ccy}->{self.base_ccy} "
                f"[{start}..{end}]"
            )
        return (r1 - r0) / r0

    # -- internals ---------------------------------------------------------

    def _lookup(
        self, base: str, quote: str, on_or_before: date,
    ) -> Optional[Decimal]:
        """Lookup with inverse-pair fallback (USD/CNY absent → try CNY/USD)."""
        assert self.rate_provider is not None  # caller guards None
        rate = self.rate_provider(base, quote, on_or_before)
        if rate is not None:
            return rate
        inv = self.rate_provider(quote, base, on_or_before)
        if inv is not None and inv != 0:
            return Decimal("1") / inv
        return None
