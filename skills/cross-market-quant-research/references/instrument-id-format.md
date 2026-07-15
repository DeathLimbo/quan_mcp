# InstrumentId Canonical Format

Format: `{market}.{venue}.{asset_type}.{symbol}`

## Markets
- `CN` — China A-share, mainland-domiciled funds/ETFs
- `US` — United States listed securities and US-domiciled funds

## Venues
- CN: `SSE` (Shanghai), `SZSE` (Shenzhen), `BSE` (Beijing), `CFFEX`, `CN_FUND`
- US: `NASDAQ`, `NYSE`, `ARCA`, `BATS`, `OTC`, `US_FUND`

## Asset types
- `EQUITY`, `ETF`, `FUND`, `INDEX`, `FX`

## Allowed combos (allow-list, others are rejected)

| Market | Asset type | Venues |
|---|---|---|
| CN | EQUITY | SSE, SZSE, BSE |
| CN | ETF | SSE, SZSE |
| CN | FUND | CN_FUND |
| CN | INDEX | SSE, SZSE, CFFEX |
| US | EQUITY | NASDAQ, NYSE, ARCA, BATS, OTC |
| US | ETF | NASDAQ, NYSE, ARCA, BATS |
| US | FUND | US_FUND |
| US | INDEX | NYSE, NASDAQ |

## Examples
- Kweichow Moutai: `CN.SSE.EQUITY.600519`
- CATL: `CN.SZSE.EQUITY.300750`
- CSI 300 ETF (Huatai): `CN.SSE.ETF.510300`
- Beijing 50: `CN.BSE.EQUITY.430047`
- Apple: `US.NASDAQ.EQUITY.AAPL`
- SPY: `US.ARCA.ETF.SPY`
- Vanguard S&P 500 Admiral (mutual fund): `US.US_FUND.FUND.VFIAX`
- ChinaAMC CSI 300 Index (open-end fund): `CN.CN_FUND.FUND.000051`

## Disambiguation rules
1. If a user gives just `600519`, ask if they mean SSE (they almost always do).
2. If a user gives just `AAPL`, default to `US.NASDAQ.EQUITY.AAPL` after confirming.
3. Never assume ADRs equal locals: `BABA` (US.NYSE.EQUITY.BABA) ≠ `09988.HK` (out of scope).
4. Symbols stored upper-case, stripped of whitespace.
