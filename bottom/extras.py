"""The layers on top of the five conditions: news digest, systemic-break cap and catalyst."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from bottom import catalyst, digest, override, tagging
from bottom.llm import Claude, ToolCaller, describe_error
from bottom.snapshot import Snapshot
from bottom.sources import news, sec
from bottom.store import RawStore
from bottom.verdict import Report


def digest_context(report: Report) -> str:
    lines = [f"Date: {report.as_of:%Y-%m-%d}."]
    dd = report.drawdown
    if dd.pct is not None:
        lines.append(f"The S&P 500 is {abs(dd.pct):.1f}% below its one-year high.")
    leader = next((r for r in report.results if r.key == "sector"), None)
    if leader and leader.met is not None:
        lines.append(f"Current leading sector fund versus the S&P 500: {leader.value}.")
    return " ".join(lines)


def enrich(
    report: Report,
    snap: Snapshot,
    cfg: dict[str, Any],
    env: Mapping[str, str],
    today: date,
    store: RawStore,
    state_path: Path,
    llm: ToolCaller | None = None,
) -> Report:
    if llm is None and env.get("ANTHROPIC_API_KEY"):
        llm = Claude(env["ANTHROPIC_API_KEY"], cfg["llm"]["model"])

    # News digest
    headlines, problems = news.fetch_headlines(env.get("FINNHUB_API_KEY"), today, store)
    report.errors.update(problems)
    if llm is None:
        report.errors["digest"] = "ANTHROPIC_API_KEY is not set"
    elif headlines:
        try:
            report.digest = digest.make_digest(headlines, digest_context(report), llm)
            store.save_json("digest.json", report.digest)
        except RuntimeError as exc:  # no headlines, or no structured reply
            report.errors["digest"] = str(exc)
        except Exception as exc:  # the digest is optional; the verdict stands without it
            report.errors["digest"] = describe_error(exc)

    # Tags for every headline, plus news on large companies in each sector
    n = cfg["news"]
    company, problems = news.fetch_company_headlines(
        env.get("FINNHUB_API_KEY"), today, n["companies"], int(n["per_company"]), store
    )
    report.errors.update(problems)
    everything = news.dedupe(headlines + company)
    if llm is not None and everything:
        try:
            report.tagged_news = tagging.tag_headlines(
                everything, n["lines"], llm, int(n["batch_size"])
            )
            store.save_json("tagged_news.json", report.tagged_news)
        except RuntimeError as exc:  # no structured reply
            report.errors["news tags"] = str(exc)
        except Exception as exc:  # tags are context only; the verdict stands without them
            report.errors["news tags"] = describe_error(exc)

    # Systemic-break cap
    report.override = override.check(snap.hy_spread, report.as_of, cfg, report.digest)

    # Catalyst: NVIDIA 8-K filings
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    user_agent = env.get("SEC_USER_AGENT")
    if not user_agent:
        report.errors["sec"] = "SEC_USER_AGENT is not set"
    else:
        try:
            filings = sec.recent_8ks(user_agent, today, int(cfg["catalyst"]["lookback_days"]))
            state, problems = catalyst.update(
                state, filings, lambda f: sec.filing_text(f, user_agent), llm
            )
            if problems:
                report.errors["sec"] = "; ".join(problems)
        except RuntimeError as exc:
            report.errors["sec"] = str(exc)
    state = catalyst.update_watch(state, report.digest, report.as_of)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=1) + "\n")
    report.catalyst = catalyst.status(state, report.as_of, cfg)
    return report
