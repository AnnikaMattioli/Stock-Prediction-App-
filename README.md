# Stock Prediction App

## Phase 1 · Alpaca Basic data check

Alpaca documents historical US stock data since 2016 on Basic. The Historical
Stock Bars API accepts `feed=sip` for consolidated US stock data on Basic when
the request's end time is at least 15 minutes old. The check uses daily OHLCV
with `adjustment=split` and separately requests raw bars for the price and split
comparisons. [Alpaca plans](https://docs.alpaca.markets/us/docs/about-market-data-api) ·
[Market Data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq) ·
[Historical Stock Bars](https://docs.alpaca.markets/us/reference/stockbars)

Use Python 3.11 or newer with an installed IANA time zone database (included
with macOS and most Linux systems). No Python packages are required. Generate
paper API keys in your Alpaca dashboard. In a local zsh terminal at the
repository root, enter the keys at the hidden prompts.

```zsh
read -rs 'APCA_API_KEY_ID?Alpaca paper API key ID: '; printf '\n'
read -rs 'APCA_API_SECRET_KEY?Alpaca paper API secret: '; printf '\n'
export APCA_API_KEY_ID APCA_API_SECRET_KEY
python3 check_alpaca.py
```

If using live-account keys, run `python3 check_alpaca.py --live` to select the
live calendar endpoint. Both modes use the same historical data endpoint.
The script reads credentials from the environment and prints no keys.

### What counts as a pass

- All six symbols return daily bars beginning in 2016 and reaching the latest
  eligible session. The report shows each symbol's actual first and last dates,
  count and latest OHLCV. It follows pagination to count the full response.
- The February 23, 2024 raw closes for AAPL and WMT match the published
  $182.52 and $175.56 within one cent. [AAPL price history](https://www.quantellect.ai/us/stocks/nasdaq/aapl/price-history/2024-02) ·
  [Walmart split history](https://stock.walmart.com/stock-data/stock-split-history)
- Across Walmart's February 26, 2024 split, the February 23 OHLC values divide
  by three within one cent and volume triples. February 26 raw and split-adjusted
  bars agree. [Walmart SEC filing](https://stock.walmart.com/sec-filings/all-sec-filings/content/0000104169-24-000033/wmt-20240223.htm)
- All six bars for the most recent completed session are present once the
  scheduled close is at least 15 minutes old. Use the reported close from
  Alpaca's calendar, which includes early closes. [Market calendar](https://docs.alpaca.markets/us/v1.4.2/reference/getcalendar-1)

Run shortly before and again shortly after close plus 15 minutes on a trading
day. The earlier run checks the previous eligible session and reports
`PENDING` (exit 2) if today's session has closed but is still inside the delay.
The later run should report today's date for all six symbols and `PASS`
(exit 0). If bars are missing, record the observation time and rerun to see
when they appear. A failed check exits 1. The 15-minute rule applies to the
request's end time. The time printed by the check records when it ran, and
availability near the cutoff must be observed with a live run. Alpaca's daily
bar timestamp identifies its New York trading date. Later corrections can
change the data. [Market Data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)

### Offline tests

From the repository root, run the tests with simulated Alpaca responses and a
command-line check for missing credentials.

```sh
python3 -m unittest discover -s tests -v
```
