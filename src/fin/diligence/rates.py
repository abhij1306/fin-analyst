"""Exchange rates the user states for an engagement (`fin engagement rate`, SPEC-17 item 2).

An order in a currency other than INR has no rupee amount until the user states a rate for its
currency as at the orderbook's date: then it counts at the rate times its value (the view
`orderbook_valued`, db/schema/fdd_orderbook.sql, read by the backlog, revenue visibility and the
orderbook reconciliation). fin never fetches or assumes a rate. Each rate is appended to
`engagement_rates` and to the audit table `diligence_events` (kind 'rate') with who stated it;
stating one again for the same currency and date replaces it from then on. Both the CLI and the
web UI call `state_rate` and `list_rates`.
"""

import json
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from fin.actor import label
from fin.audit.errors import RateInvalid
from fin.audit.logger import Run
from fin.db.conn import transaction, utcnow
from fin.db.rows import fetch_dicts
from fin.diligence.engagement import company_database, require_open

CURRENCY = re.compile(r"^[A-Z]{3}$")
ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")  # fromisoformat also takes 2025-W13-1 and 20250331
RATE_LIMIT = Decimal(10) ** 14  # engagement_rates.rate is DECIMAL(20,6)


def parse_rate(currency: str, rate: str, as_at: str) -> tuple[str, Decimal, date]:
    """(currency, rate, as_at) checked. E962 for a currency that is not three letters or is INR,
    a rate that is not above 0 with at most six decimals, or a date that is not YYYY-MM-DD."""
    code = currency.strip().upper()
    if not CURRENCY.match(code) or code == "INR":
        raise RateInvalid(
            f"{currency!r} is not a currency to convert (three letters, not INR, e.g. USD); "
            "nothing stored"
        ).with_context(currency=currency)
    try:
        value = Decimal(rate.strip())
        exact = value.is_finite() and value == value.quantize(Decimal("0.000001"))
    except InvalidOperation:
        exact = False
    if not exact or value <= 0 or value >= RATE_LIMIT:
        raise RateInvalid(
            f"{rate!r} is not a rate in rupees per {code} (a number above 0 with at most six "
            "decimals, e.g. 84.10); nothing stored"
        ).with_context(rate=rate)
    try:
        if not ISO_DAY.match(as_at.strip()):
            raise ValueError(as_at)
        day = date.fromisoformat(as_at.strip())
    except ValueError as exc:
        raise RateInvalid(
            f"{as_at!r} is not a date (use YYYY-MM-DD, the orderbook's as-at date); nothing stored"
        ).with_context(as_at=as_at) from exc
    return code, value, day


def state_rate(
    run: Run, company: str, currency: str, rate: str, as_at: str, actor: str
) -> dict[str, Any]:
    """Store the rate the user states for the open engagement: rupees per unit of `currency` as at
    `as_at`. Returns {"rate": the row, "orders": how many loaded orders in that currency are as at
    that date}. `actor`: who states it (`fin.actor`: the UI passes USER, the CLI
    `from_environment()`). E962 for a value `parse_rate` refuses; E803 when no engagement is
    open."""
    code, value, day = parse_rate(currency, rate, as_at)
    with company_database(run, company, write=True) as con, transaction(con):
        engagement_id = require_open(con, company)["engagement_id"]
        [row] = fetch_dicts(
            con,
            "INSERT INTO engagement_rates (engagement_id, currency, rate, as_at, actor, run_id, "
            "stated_at) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING *",
            [engagement_id, code, value, day, actor, run.run_id, utcnow()],
        )
        run.record(
            "diligence_events",
            {
                "company": company,
                "engagement_id": engagement_id,
                "kind": "rate",
                "item_id": str(row["rate_id"]),
                "status": "stated",
                "detail": json.dumps(row, default=str),
            },
        )
        found = con.execute(
            "SELECT count(*) FROM orderbook_valued WHERE engagement_id = ? AND file_status = "
            "'loaded' AND currency = ? AND as_at = ?",
            [engagement_id, code, day],
        ).fetchone()
    return {"company": company, "rate": row, "orders": found[0] if found else 0}


def list_rates(run: Run, company: str) -> list[dict[str, Any]]:
    """The rates in use for the open engagement (the latest per currency and date). E803 when no
    engagement is open."""
    with company_database(run, company) as con:
        engagement_id = require_open(con, company)["engagement_id"]
        return fetch_dicts(
            con,
            "SELECT * FROM engagement_rates_current WHERE engagement_id = ? "
            "ORDER BY currency, as_at",
            [engagement_id],
        )


def format_rate(row: dict[str, Any]) -> str:
    return (
        f"{row['currency']} {row['rate']} rupees as at {row['as_at']} (#{row['rate_id']}, "
        f"stated by {label(row['actor'])} {row['stated_at']:%Y-%m-%d})"
    )


def format_stated(result: dict[str, Any]) -> str:
    row = result["rate"]
    line = f"{result['company']}: rate {format_rate(row)}"
    if result["orders"]:
        return line + f"; {result['orders']} {row['currency']} orders as at that date now count"
    return line + (
        f"; no loaded {row['currency']} order is as at {row['as_at']}: a rate counts only for an "
        "orderbook as at the same date"
    )


def format_rates(company: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return f"{company}: no exchange rate stated; orders not in INR have no rupee amount"
    return "\n".join([f"{company}: exchange rates"] + [f"  {format_rate(r)}" for r in rows])
