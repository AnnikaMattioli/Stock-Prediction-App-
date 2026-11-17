"""Streamlit dashboard for the five-stock research portfolio."""

import json
import os
from datetime import datetime
from pathlib import Path

import pyarrow.parquet as pq
import streamlit as st

from backtest import simulate_evaluation
from baseline_report import STOCKS, build_rows, load_bars
from check_alpaca import NY
from evaluate_model import evaluate, fit_predict, summarize


@st.cache_data(max_entries=2, show_spinner="Calculating forecasts and held-out results…")
def dashboard_data(path, modified_at_ns):
    with path.open("rb") as snapshot:
        metadata = json.loads(pq.read_schema(snapshot).metadata[b"stock_prediction"])
        snapshot.seek(0)
        sessions, bars = load_bars(snapshot, include_open=True)
    rows = list(build_rows(sessions, bars))
    latest = [row for row in rows if row["forecast_session"] == sessions[-1]]
    forecasts, _ = fit_predict(rows, latest)
    validation, (held_out, audit) = evaluate(rows)
    trades = simulate_evaluation(validation, held_out, sessions, bars)
    return sessions, bars, metadata, forecasts, summarize(held_out), audit, trades


def main():
    st.set_page_config(page_title="Stock Forecasts", page_icon="📈", layout="wide")
    st.title("Stock Forecasts")
    st.markdown("Probability that the next US trading session closes above the forecast session’s split-adjusted close.")
    path = Path(os.environ.get("STOCK_CACHE_PATH", "data/daily_bars.parquet"))
    st.button("Reload local snapshot")
    try:
        sessions, bars, metadata, forecasts, scores, audit, trades = dashboard_data(
            path, path.stat().st_mtime_ns,
        )
    except FileNotFoundError:
        st.warning("No local bar cache found. Run the updater from the project folder, then reload.")
        st.code(".venv/bin/python update_data.py", language="shell")
        return
    except (OSError, ValueError, KeyError, TypeError) as error:
        st.error(f"Unable to load the snapshot or calculate results. {error}")
        return

    received = datetime.fromisoformat(metadata["received_at"]).astimezone(NY)
    st.caption(f"Forecast session {sessions[-1]}  ·  Data received {received:%Y-%m-%d %H:%M:%S %Z}  ·  Alpaca SIP")
    st.caption("Forecasts use the displayed local snapshot. Reload after running the updater to pick up new bars.")
    with st.grid(5, min_column_width=120):
        for row in forecasts:
            st.metric(row["symbol"], f'{row["logistic_probability"]:.1%}', border=True)

    st.header("Price history")
    symbol = st.selectbox("Stock", STOCKS)
    st.subheader(f"{symbol} split-adjusted close")
    plot = {"Session": sessions[-252:], "Close (USD)": [bars[symbol][day]["close"] for day in sessions[-252:]]}
    st.line_chart(plot, x="Session", y="Close (USD)", height=300, alt=f"{symbol} daily closing prices")

    st.header("Held-out results")
    st.caption(
        f"Forecast sessions {audit['first_forecast_session']} to {audit['last_forecast_session']} "
        f"· {audit['sessions']} sessions"
    )
    methods = (
        ("Logistic regression", "logistic"),
        ("Always up", "always_up"),
        ("Historical up-rate", "historical_up_rate"),
    )
    for label, group in (("All stocks", scores["overall"]), (symbol, scores["by_stock"][symbol])):
        st.subheader(label)
        st.table([
            {"Method": name, "Accuracy": f"{group[key]['accuracy']:.2%}", "Brier": f"{group[key]['brier']:.5f}"}
            for name, key in methods
        ])

    st.subheader("Next-session trading simulation")
    test = trades["test"]
    st.caption(
        f"Execution sessions {test['first_execution_session']} to {test['last_execution_session']} "
        f"· {trades['cost_bps_per_side']} basis points per side"
    )
    threshold = trades["selected_threshold"]
    rule = f"{threshold:.0%} threshold" if threshold is not None else "Cash"
    st.table([
        {
            "Rule": name,
            "Net return": f"{stats['net_return']:+.2%}",
            "Max drawdown": f"{stats['max_net_drawdown']:.2%}",
            "Daily turnover": f"{stats['mean_daily_two_way_turnover']:.2%}",
        }
        for name, stats in ((rule, test), ("Always invested intraday", trades["test_always_invested"]))
    ])
    st.caption("The historical snapshot includes revised bars. Simulated orders use daily open and close prices as fill proxies.")


if __name__ == "__main__":
    main()
