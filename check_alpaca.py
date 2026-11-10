"""Probe historical SIP daily bars with an Alpaca Basic account."""

import argparse
import json
import os
import sys
from datetime import datetime, time, timedelta
from math import isclose
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

SYMBOLS = ("AAPL", "MSFT", "JPM", "XOM", "WMT", "SPY")
NY = ZoneInfo("America/New_York")
SIP_DELAY = timedelta(minutes=15)
DATA_URL = "https://data.alpaca.markets/v2/stocks/bars"
PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"


def get_json(url, headers, **params):
    request = Request(f"{url}?{urlencode(params)}", headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except HTTPError as error:
        detail = ""
        if error.code in (401, 403):
            detail = " Check the account's API keys, environment and SIP entitlement."
        raise RuntimeError(f"{url} returned HTTP {error.code}.{detail}") from None
    except URLError as error:
        raise RuntimeError(f"Could not reach {url}: {error.reason}") from None


def fetch_bars(headers, symbols, start, end, adjustment):
    params = {
        "symbols": ",".join(symbols),
        "timeframe": "1Day",
        "feed": "sip",
        "adjustment": adjustment,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "sort": "asc",
        "limit": 10_000,
    }
    results = {symbol: {} for symbol in symbols}
    while True:
        page = get_json(DATA_URL, headers, **params)
        for symbol, bars in (page["bars"] or {}).items():
            for bar in bars:
                session = datetime.fromisoformat(bar["t"]).astimezone(NY).date()
                if session in results[symbol]:
                    raise RuntimeError(f"Duplicate bar for {symbol} on {session}")
                results[symbol][session] = bar
        token = page.get("next_page_token")
        if not token:
            return results
        if token == params.get("page_token"):
            raise RuntimeError("Alpaca repeated the pagination token")
        params["page_token"] = token


def run_check(headers, trading_url):
    today = datetime.now(NY).date()
    calendar = get_json(
        f"{trading_url}/v2/calendar",
        headers,
        start=today - timedelta(days=14),
        end=today,
    )
    now = datetime.now(NY)
    cutoff = now - SIP_DELAY
    closes = [
        datetime.fromisoformat(f"{day['date']}T{day['close']}").replace(tzinfo=NY)
        for day in calendar
    ]
    completed = max((close for close in closes if close <= now), default=None)
    eligible = max((close for close in closes if close <= cutoff), default=None)
    if eligible is None:
        raise RuntimeError("No completed session outside the SIP delay in the calendar response")

    expected = eligible.date()
    start = datetime(2016, 1, 1, tzinfo=NY)
    # Exclude any in-progress session even if Alpaca has published a partial bar.
    end_of_session_date = datetime.combine(expected, time.max, NY)
    end = min(cutoff, end_of_session_date)
    print(f"Checked at {now.isoformat()} | SIP 1Day split-adjusted")
    print(f"Requested {start.date()} through {end.isoformat()}")
    print(f"Latest completed session closed at {completed.isoformat()}")
    print(f"Close + 15 minutes: {(completed + SIP_DELAY).isoformat()}")
    print(f"Checking latest eligible session {expected}")
    history = fetch_bars(headers, SYMBOLS, start, end, "split")
    print(f"History received at {datetime.now(NY).isoformat()}")

    passed = True
    for symbol, bars in history.items():
        if not bars:
            print(f"FAIL {symbol}: no bars returned")
            passed = False
            continue
        first, last = min(bars), max(bars)
        sample = bars[last]
        valid = first.year == 2016 and last == expected
        passed &= valid
        print(
            f"{'PASS' if valid else 'FAIL'} {symbol}: {len(bars):,} bars {first} to {last} "
            f"| latest O/H/L/C/V " + "/".join(str(sample[field]) for field in "ohlcv")
        )

    before = datetime(2024, 2, 23, tzinfo=NY)
    after = datetime(2024, 2, 26, tzinfo=NY)
    raw = fetch_bars(headers, ("AAPL", "WMT"), before, after, "raw")
    for symbol, expected_close in (("AAPL", 182.52), ("WMT", 175.56)):
        actual = raw[symbol].get(before.date(), {}).get("c")
        valid = actual is not None and isclose(actual, expected_close, abs_tol=0.01)
        passed &= valid
        print(
            f"{'PASS' if valid else 'FAIL'} {symbol} {before.date()} raw close: "
            f"{actual}, published {expected_close:.2f}"
        )

    for day, factor in ((before.date(), 3), (after.date(), 1)):
        unadjusted = raw["WMT"].get(day)
        adjusted = history["WMT"].get(day)
        valid = bool(
            unadjusted and adjusted
            and all(isclose(adjusted[key], unadjusted[key] / factor, abs_tol=0.01) for key in "ohlc")
            and adjusted["v"] == unadjusted["v"] * factor
        )
        passed &= valid
        print(
            f"{'PASS' if valid else 'FAIL'} WMT {day} split adjustment: "
            f"OHLC / {factor}, volume × {factor}"
        )

    if not passed:
        print("FAIL: see the checks above.")
        return 1
    if completed != eligible:
        print("PENDING: rerun after the latest close plus 15 minutes to check that session.")
        return 2
    print("PASS: history, published closes, split adjustment and latest eligible session.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="use the live calendar with live API keys")
    args = parser.parse_args()
    key = os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        print("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY in your environment.", file=sys.stderr)
        return 1
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    try:
        return run_check(headers, LIVE_URL if args.live else PAPER_URL)
    except (RuntimeError, ValueError, KeyError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
