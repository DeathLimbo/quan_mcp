"""FX conversion package (spec §3.2, §2.1.6, §12.6, §29).

Point-in-time currency conversion + realised FX return attribution. Fail-closed
by design: a missing rate raises ``FxNotAvailableError`` rather than silently
using a stale/default value, so cross-currency attribution can never fabricate
returns (spec §38 风控: 数据/FX 异常触发 NO_FORECAST).
"""
from packages.fx.converter import (
    FxConverter,
    FxNotAvailableError,
    RateProvider,
)

__all__ = ["FxConverter", "FxNotAvailableError", "RateProvider"]
