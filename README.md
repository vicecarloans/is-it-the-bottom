# Is it the bottom?

[![CI](https://github.com/vicecarloans/is-it-the-bottom/actions/workflows/ci.yml/badge.svg)](https://github.com/vicecarloans/is-it-the-bottom/actions/workflows/ci.yml)

A daily check of five conditions that have marked past stock market bottoms, turned into one verdict: **No**, **Getting closer**, or **Looks like a bottom**.

> A hobby project, not financial advice. The rules have not been backtested yet.

## The five conditions

They are checked only while the S&P 500 (SPY) is at least 10% below its 52-week high.

| # | Condition | Met when |
| --- | --- | --- |
| 1 | VIX above 30 | VIX closed above 30 in the last 10 trading days |
| 2 | Fed not signaling hikes | 2-year Treasury yield minus the upper fed funds target is at most +0.10 pts, and no hike in the last 56 days |
| 3 | Margin debt deleveraging | FINRA margin debt is 10% or more below its 12-month high (stale data counts as not met) |
| 4 | A clear leading sector | The top sector ETF beats SPY by 5+ pts over 20 days and has led on 10 of the last 15 days |
| 5 | NVIDIA beats earnings | NVIDIA beat EPS consensus in a report from the drawdown or the last 90 days |

Verdict: 0–2 met is **No**, 3–4 is **Getting closer**, 5 is **Looks like a bottom**. All thresholds live in [`config.yaml`](config.yaml).

## Run it

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
export FRED_API_KEY=...      # free: https://fred.stlouisfed.org/docs/api/api_key.html
export FINNHUB_API_KEY=...   # free: https://finnhub.io/register
uv run bottom check
```

`bottom check --as-of 2026-03-02` evaluates a past date with the data available today. Raw responses are saved under `data/raw/<date>/`; pass `--no-save` to skip that.

`bottom run` does the same checks and writes `data/latest.json` and `data/history.json`, which the page reads.

## How it runs

- **Daily job:** [`.github/workflows/daily.yml`](.github/workflows/daily.yml) runs `bottom run` on weekdays at 22:30 UTC and commits the two JSON files. It also runs when the code, `config.yaml` or the FINRA spreadsheet changes on `main`. Each run's checklist is in its GitHub Actions summary.
- **Page:** a static page in [`site/`](site/), hosted on Vercel (`vercel.json` points it at `site/`). It loads the JSON straight from this repo, so new data shows up without a redeploy.
- **Secrets:** `FRED_API_KEY` and `FINNHUB_API_KEY`, set as GitHub Actions secrets.

To preview the page locally with made-up data:

```bash
uv run python scripts/sample_data.py
python -m http.server -d site 8000
# open http://localhost:8000/?data=sample/
```

## Data sources

| Input | Source |
| --- | --- |
| SPY, sector ETFs, VIX | yfinance, with Stooq as a fallback for ETFs |
| 2-year yield, Fed target, VIX fallback, high-yield spread | [FRED](https://fred.stlouisfed.org/) |
| Margin debt | [FINRA margin statistics](https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics), see below |
| NVIDIA earnings | [Finnhub](https://finnhub.io/) |

### Updating the margin data

FINRA offers no data feed and may refuse automated downloads. When it does, the job reads the copy at `data/finra/margin-statistics.xlsx`. FINRA publishes a new month around the third week of the following month; to update, download the spreadsheet from the [FINRA page](https://www.finra.org/rules-guidance/key-topics/margin-accounts/margin-statistics) and replace that file. If the copy is more than 60 days past its latest month, condition 3 shows as stale and counts as not met.

## Development

```bash
uv run pytest
uv run ruff check .
```

## License

MIT
