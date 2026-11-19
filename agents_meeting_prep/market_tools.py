"""Self-contained market data & Finnhub news tools for agents_meeting_prep.

Primary functions use business-context naming (get_benchmark_returns,
get_approved_commentary, get_fund_news_summary, get_portfolio_beta,
get_market_indicators) with legacy aliases preserved for skill compatibility.
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Callable, List, Optional

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
            hist_1y = tk.history(period="1y", auto_adjust=True)["Close"]
            hist_ytd = tk.history(start=year_start, auto_adjust=True)["Close"]
            hist_2024 = tk.history(start="2024-01-01", end="2024-12-31", auto_adjust=True)["Close"]

            current_price = round(float(hist_1y.iloc[-1]), 2) if len(hist_1y) >= 1 else None
            return_1y = round(((float(hist_1y.iloc[-1]) / float(hist_1y.iloc[0])) - 1) * 100, 2) if len(hist_1y) >= 2 else None
            return_1d = round(((float(hist_1y.iloc[-1]) / float(hist_1y.iloc[-2])) - 1) * 100, 2) if len(hist_1y) >= 2 else None
            return_ytd = round(((float(hist_ytd.iloc[-1]) / float(hist_ytd.iloc[0])) - 1) * 100, 2) if len(hist_ytd) >= 2 else None
            return_2024 = round(((float(hist_2024.iloc[-1]) / float(hist_2024.iloc[0])) - 1) * 100, 2) if len(hist_2024) >= 2 else None

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
            hist = yf.Ticker(sym).history(period="5d")["Close"]
            result[key] = round(float(hist.iloc[-1]), 2) if not hist.empty else None
        return result
    except Exception as e:
        logger.warning("get_market_indicators failed: %s", e)
        return {}


def get_portfolio_beta(holdings: list[dict]) -> dict:
    """Compute weighted portfolio beta vs SPY using 1Y daily returns."""
    try:
        import numpy as np
        import yfinance as yf

        spy = yf.Ticker("SPY").history(period="1y", auto_adjust=True)["Close"].pct_change().dropna()
        if spy.empty:
            return {"portfolio_beta": None}

        weighted_beta = 0.0
        total_weight = 0.0
        for h in holdings or []:
            ticker = h.get("ticker") or h.get("instrument_id")
            weight = float(h.get("weight") or h.get("actual_weight") or 0.0)
            if not ticker or ticker in ("CASH", "MMKT") or weight <= 0:
                continue
            hist = yf.Ticker(ticker).history(period="1y", auto_adjust=True)["Close"].pct_change().dropna()
            aligned = hist.align(spy, join="inner")
            if len(aligned[0]) < 20:
                continue
            cov = float(np.cov(aligned[0], aligned[1])[0][1])
            var = float(np.var(aligned[1]))
            if var > 0:
                weighted_beta += (cov / var) * weight
                total_weight += weight

        beta = round(weighted_beta / total_weight, 2) if total_weight > 0 else None
        return {"portfolio_beta": beta}
    except Exception as e:
        logger.warning("get_portfolio_beta failed: %s", e)
        return {"portfolio_beta": None}


def _fetch_finnhub_company_news(symbol: str, days: int = 14) -> list[dict]:
    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not api_key:
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


def get_fund_news_summary(tickers: List[str]) -> dict:
    """Fetch recent Finnhub headlines for a list of fund/ETF tickers."""
    return {
        t: {
            "recent_headlines": [a["headline"] for a in _fetch_finnhub_company_news(t)],
        }
        for t in (tickers or [])
        if t and t not in ("CASH", "MMKT")
    }


# Backward-compatible aliases for unchanged skill prompts
yfinance_price = get_benchmark_returns
finnhub_news_with_benchmarks = get_approved_commentary
