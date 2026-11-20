"""Self-contained market data & Finnhub news tools for agents_meeting_prep.

Primary functions use business-context naming (get_benchmark_returns,
get_approved_commentary, get_fund_news_summary, get_portfolio_beta,
get_market_indicators).
"""

import logging
import math
import os
from datetime import datetime, timedelta
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)

_BENCHMARK_TICKER_MAP: dict[str, Optional[str]] = {
    "S&P 500": "SPY",
    "Russell 1000 Growth": "SPY",
    "Russell 1000": "SPY",
    "Russell 2000": "IWM",
    "Bloomberg US Agg": "AGG",
    "Bloomberg Aggregate": "AGG",
    "US Aggregate Bond": "AGG",
    "MSCI ACWI ex-US": "EFA",
    "MSCI EAFE": "EFA",
    "MSCI World": "SPY",
    "NASDAQ": "QQQ",
    "US T-Bill 90-day": None,
}

_FUND_DISPLAY_NAMES: dict[str, str] = {
    "VFIAX": "Vanguard 500 Index",
    "FSPGX": "Fidelity Large Cap Growth",
    "ABNDX": "American Funds Bond",
    "VTIAX": "Vanguard Total International",
    "FBALX": "Fidelity Balanced",
    "VWIAX": "Vanguard Wellesley Income",
    "SPY": "S&P 500",
    "AGG": "US Aggregate Bond",
    "QQQ": "Nasdaq 100",
    "EFA": "Intl Developed Markets",
    "VEA": "Intl Developed Markets",
    "IWM": "Russell 2000",
}


def _safe_float(val: Any, ndigits: int = 2) -> Optional[float]:
    try:
        f = float(val)
        return round(f, ndigits) if math.isfinite(f) else None
    except Exception:
        return None


def get_benchmark_returns(tickers: List[str]) -> dict:
    """Fetch current price, YTD, 2024, 1Y, and 1D returns for a list of tickers."""
    try:
        import yfinance as yf
    except Exception as e:
        logger.error("yfinance import failed: %s", e)
        return {}

    out: dict = {}
    year_start = f"{datetime.now().year}-01-01"
    for ticker in tickers or []:
        if not ticker or ticker == "CASH":
            continue
        try:
            tk = yf.Ticker(ticker)
            hist_1y = tk.history(period="1y", auto_adjust=True)["Close"].dropna()
            hist_ytd = tk.history(start=year_start, auto_adjust=True)["Close"].dropna()
            hist_2024 = tk.history(start="2024-01-01", end="2024-12-31", auto_adjust=True)["Close"].dropna()

            current_price = _safe_float(hist_1y.iloc[-1]) if len(hist_1y) >= 1 else None
            return_1y = _safe_float(((float(hist_1y.iloc[-1]) / float(hist_1y.iloc[0])) - 1) * 100) if len(hist_1y) >= 2 and float(hist_1y.iloc[0]) != 0 else None
            return_1d = _safe_float(((float(hist_1y.iloc[-1]) / float(hist_1y.iloc[-2])) - 1) * 100) if len(hist_1y) >= 2 and float(hist_1y.iloc[-2]) != 0 else None
            return_ytd = _safe_float(((float(hist_ytd.iloc[-1]) / float(hist_ytd.iloc[0])) - 1) * 100) if len(hist_ytd) >= 2 and float(hist_ytd.iloc[0]) != 0 else None
            return_2024 = _safe_float(((float(hist_2024.iloc[-1]) / float(hist_2024.iloc[0])) - 1) * 100) if len(hist_2024) >= 2 and float(hist_2024.iloc[0]) != 0 else None

            out[ticker] = {
                "current_price": current_price,
                "return_ytd": return_ytd,
                "return_2024": return_2024,
                "return_1y": return_1y,
                "return_1d": return_1d,
            }
        except Exception as e:
            logger.warning("get_benchmark_returns failed for %s: %s", ticker, e)
            out[ticker] = {
                "current_price": None,
                "return_ytd": None,
                "return_2024": None,
                "return_1y": None,
                "return_1d": None,
            }
    return out


def get_market_indicators() -> dict:
    """Fetch current S&P 500 (SPY), NASDAQ (QQQ), and VIX (^VIX) indicators."""
    try:
        import yfinance as yf
        result: dict = {"as_of": datetime.now().strftime("%Y-%m-%d")}
        for key, sym in (("sp500_current", "SPY"), ("nasdaq_current", "QQQ"), ("vix_current", "^VIX")):
            hist = yf.Ticker(sym).history(period="5d")["Close"].dropna()
            result[key] = _safe_float(hist.iloc[-1]) if not hist.empty else None
        return result
    except Exception as e:
        logger.warning("get_market_indicators failed: %s", e)
        return {}


def get_portfolio_beta(
    client_id: str,
    holdings_fn: Optional[Callable[[str], list[dict]]] = None,
) -> dict:
    """Compute weighted portfolio beta vs SPY for a client's holdings."""
    if holdings_fn is None:
        return {"portfolio_beta": None, "per_fund": {}, "error": "No holdings provider"}

    try:
        import numpy as np
        import yfinance as yf

        rows = holdings_fn(client_id)
        eligible = [
            r for r in (rows or [])
            if (r.get("ticker") or r.get("instrument_id")) not in (None, "", "CASH", "MMKT")
            and float(r.get("actual_weight") or r.get("weight") or 0.0) > 0
        ]
        if not eligible:
            return {"portfolio_beta": None, "per_fund": {}, "error": "No eligible tickers"}

        spy = yf.Ticker("SPY").history(period="1y", auto_adjust=True)["Close"].pct_change().dropna()
        if spy.empty:
            return {"portfolio_beta": None, "per_fund": {}, "error": "Insufficient SPY data"}

        total_w = sum(float(r.get("actual_weight") or r.get("weight") or 0.0) for r in eligible) or 1.0
        per_fund: dict[str, float] = {}
        weighted_beta = 0.0

        for h in eligible:
            ticker = h.get("ticker") or h.get("instrument_id")
            weight = float(h.get("actual_weight") or h.get("weight") or 0.0) / total_w
            hist = yf.Ticker(ticker).history(period="1y", auto_adjust=True)["Close"].pct_change().dropna()
            aligned = hist.align(spy, join="inner")
            if len(aligned[0]) < 20:
                continue
            cov = float(np.cov(aligned[0], aligned[1])[0][1])
            var = float(np.var(aligned[1]))
            if var > 0:
                beta = round(cov / var, 3)
                per_fund[ticker] = beta
                weighted_beta += beta * weight

        if not per_fund:
            return {"portfolio_beta": None, "per_fund": {}, "error": "Could not compute beta"}
        return {"portfolio_beta": round(weighted_beta, 3), "per_fund": per_fund, "error": None}
    except Exception as e:
        logger.warning("get_portfolio_beta failed: %s", e)
        return {"portfolio_beta": None, "per_fund": {}, "error": str(e)}


def _fetch_finnhub_company_news(symbol: str, days: int = 14) -> list[dict]:
    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not api_key:
        logger.warning("FINNHUB_API_KEY is not set; skipping Finnhub company news lookup for %s.", symbol)
        return []
    try:
        import finnhub
        client = finnhub.Client(api_key=api_key)
        end = datetime.now().strftime("%Y-%m-%d")
        start = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        articles = client.company_news(symbol, _from=start, to=end) or []
        return [
            {
                "headline": a.get("headline", ""),
                "source": a.get("source", ""),
                "datetime": a.get("datetime"),
                "url": a.get("url", ""),
            }
            for a in articles[:5]
            if a.get("headline")
        ]
    except Exception as e:
        logger.warning("Finnhub news failed for %s: %s", symbol, e)
        return []


def get_approved_commentary(
    client_id: str,
    holdings_fn: Optional[Callable[[str], list[dict]]] = None,
) -> dict:
    """Fetch recent Finnhub news for a client's significant holdings (weight > 5%)."""
    if holdings_fn is None:
        return {}

    rows = holdings_fn(client_id)
    out: dict = {}
    for row in rows or []:
        if "error" in row:
            continue
        weight = float(row.get("actual_weight") or row.get("weight") or 0.0)
        if weight <= 5.0:
            continue
        ticker = row.get("ticker") or row.get("instrument_id") or ""
        if not ticker or ticker in ("CASH", "MMKT"):
            continue
        articles = _fetch_finnhub_company_news(ticker)
        sourced_from = ticker
        if not articles:
            bm = _BENCHMARK_TICKER_MAP.get(row.get("benchmark", ""))
            if bm:
                articles = _fetch_finnhub_company_news(bm)
                sourced_from = f"{bm} (benchmark)"
        out[ticker] = {
            "recent_headlines": [a["headline"] for a in articles],
            "article_count": len(articles),
            "sourced_from": sourced_from,
        }
    return out


def get_fund_news_summary(
    client_id: str,
    holdings_fn: Optional[Callable[[str], list[dict]]] = None,
) -> list[dict]:
    """Build advisor-facing fund performance summaries for a client's significant holdings (>5%)."""
    if holdings_fn is None:
        return []

    rows = holdings_fn(client_id)
    sig = [
        r for r in (rows or [])
        if float(r.get("actual_weight") or r.get("weight") or 0.0) > 5.0
        and (r.get("ticker") or r.get("instrument_id")) not in (None, "", "CASH", "MMKT")
    ]
    sig.sort(key=lambda r: float(r.get("actual_weight") or r.get("weight") or 0.0), reverse=True)
    if not sig:
        return []

    tickers = [r.get("ticker") or r.get("instrument_id") for r in sig[:5]]
    prices = get_benchmark_returns(tickers)

    items: list[dict] = []
    for r in sig[:5]:
        ticker = r.get("ticker") or r.get("instrument_id")
        fund_name = _FUND_DISPLAY_NAMES.get(ticker, r.get("fund_name") or ticker)
        weight = round(float(r.get("actual_weight") or r.get("weight") or 0.0), 1)
        p = prices.get(ticker, {})
        items.append({
            "ticker": ticker,
            "fund_name": fund_name,
            "asset_class": r.get("asset_class") or "Fund",
            "weight_pct": weight,
            "current_price": p.get("current_price"),
            "return_ytd": p.get("return_ytd"),
            "return_1y": p.get("return_1y"),
        })
    return items
