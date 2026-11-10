"""Backfill or refresh the local Alpaca SIP daily bars cache."""

import argparse
import json
import os
import sys
from datetime import date, datetime, time, timezone
from fcntl import LOCK_EX, LOCK_NB, flock
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory

import pyarrow as pa
import pyarrow.parquet as pq

from check_alpaca import DATA_URL, LIVE_URL, NY, PAPER_URL, SIP_DELAY, SYMBOLS, fetch_bars, get_json

START_DATE = date(2016, 1, 1)
ADJUSTMENT = "split"
SCHEMA = pa.schema([
    ("symbol", pa.string()),
    ("session", pa.date32()),
    ("bar_timestamp", pa.timestamp("us", tz="UTC")),
    ("open", pa.float64()),
    ("high", pa.float64()),
    ("low", pa.float64()),
    ("close", pa.float64()),
    ("volume", pa.int64()),
])


def cache_rows(history, sessions):
    rows = []
    for symbol in SYMBOLS:
        bars = history[symbol]
        missing = sessions - bars.keys()
        unexpected = bars.keys() - sessions
        if missing or unexpected:
            examples = ", ".join(map(str, sorted(missing | unexpected)[:5]))
            raise RuntimeError(
                f"{symbol}: {len(missing)} missing and {len(unexpected)} unexpected "
                f"sessions (first affected dates: {examples})"
            )
        for session, bar in sorted(bars.items()):
            opening, high, low, close = (bar[key] for key in "ohlc")
            volume = bar["v"]
            if (
                not all(isfinite(price) and price > 0 for price in (opening, high, low, close))
                or not low <= min(opening, close) <= max(opening, close) <= high
                or type(volume) is not int
                or volume < 0
            ):
                raise RuntimeError(f"{symbol}: invalid OHLCV for {session}")
            rows.append({
                "symbol": symbol,
                "session": session,
                "bar_timestamp": datetime.fromisoformat(bar["t"]).astimezone(timezone.utc),
                "open": opening,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            })
    return rows


def refresh(headers, trading_url, output):
    # The lock covers fetching through publication, so an older run cannot overwrite a newer one.
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.with_suffix(output.suffix + ".lock").open("ab") as lock:
        try:
            flock(lock, LOCK_EX | LOCK_NB)
        except BlockingIOError:
            raise RuntimeError(f"Another update is running for {output}") from None

        calendar = get_json(
            f"{trading_url}/v2/calendar",
            headers,
            start=START_DATE,
            end=datetime.now(NY).date(),
        )
        checked_at = datetime.now(NY)
        cutoff = checked_at - SIP_DELAY
        sessions = set()
        for day in calendar:
            close = datetime.fromisoformat(f"{day['date']}T{day['close']}").replace(tzinfo=NY)
            if close <= cutoff:
                sessions.add(close.date())
        if not sessions:
            raise RuntimeError("No eligible trading sessions returned by Alpaca's calendar")

        latest = max(sessions)
        start = datetime.combine(START_DATE, time.min, NY)
        end = min(cutoff, datetime.combine(latest, time.max, NY))
        history = fetch_bars(headers, SYMBOLS, start, end, ADJUSTMENT)
        received_at = datetime.now(timezone.utc)
        rows = cache_rows(history, sessions)
        metadata = {
            "schema_version": 1,
            "source": DATA_URL,
            "calendar_source": f"{trading_url}/v2/calendar",
            "feed": "sip",
            "timeframe": "1Day",
            "adjustment": ADJUSTMENT,
            "symbols": SYMBOLS,
            "request_start": start.isoformat(),
            "request_end": end.isoformat(),
            "first_session": min(sessions).isoformat(),
            "last_session": latest.isoformat(),
            "checked_at": checked_at.isoformat(),
            "received_at": received_at.isoformat(),
            "session_count": len(sessions),
            "row_count": len(rows),
        }
        schema = SCHEMA.with_metadata({b"stock_prediction": json.dumps(metadata).encode()})
        table = pa.Table.from_pylist(rows, schema=schema)
        with TemporaryDirectory(dir=output.parent, prefix=f".{output.name}.") as temp:
            staged = Path(temp) / output.name
            pq.write_table(table, staged)
            with staged.open("rb") as saved:
                os.fsync(saved.fileno())
            staged.replace(output)
    return metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/daily_bars.parquet"))
    parser.add_argument("--live", action="store_true", help="use the live calendar with live API keys")
    args = parser.parse_args()
    key = os.environ.get("APCA_API_KEY_ID")
    secret = os.environ.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        print("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY in your environment.", file=sys.stderr)
        return 1
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    try:
        report = refresh(headers, LIVE_URL if args.live else PAPER_URL, args.output.resolve())
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    print(f"Saved {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
