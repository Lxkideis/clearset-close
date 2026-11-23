"""Pinned report subsets. Packet metadata is ours, not part of Stripe's API."""

from datetime import datetime, timezone
from decimal import Decimal
import csv, hashlib, io, json, re

EXPONENT = {"SGD": 2, "USD": 2, "JPY": 0}
SOURCES = {
    "ledger",
    "activity",
    "membership",
    "payouts",
    "movements",
    "bank",
    "summary",
}
MONEY_LIMIT = 9_000_000_000_000


class ContractError(ValueError):
    pass


def timestamp(v):
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            raise ValueError()
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        raise ContractError("Timestamps must include an explicit UTC offset")


def key(v):
    if not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_:.-]{1,120}", v):
        raise ContractError("Invalid identifier")
    return v


def currency(v):
    result = str(v).upper()
    if result not in EXPONENT:
        raise ContractError("Supported settlement currencies: SGD, USD, JPY")
    return result


def minor(v):
    if isinstance(v, bool) or not re.fullmatch(r"-?\d+", str(v)):
        raise ContractError("API/ledger money must be integer minor units")
    result = int(v)
    if abs(result) > MONEY_LIMIT:
        raise ContractError("Amount outside the lab contract")
    return result


def major(v, cur):
    if not re.fullmatch(r"-?\d+(\.\d+)?", str(v)):
        raise ContractError("Report money must be a plain decimal string")
    amount = Decimal(str(v)) * (10 ** EXPONENT[cur])
    if amount != amount.to_integral_value():
        raise ContractError("Money precision would require rounding")
    return minor(str(int(amount)))


CSV_FIELDS = {
    "ledger": [
        "event_id",
        "source_id",
        "reporting_category",
        "currency",
        "gross_minor",
        "expected_fee_minor",
        "occurred_utc",
        "partner",
    ],
    "activity": [
        "balance_transaction_id",
        "source_id",
        "reporting_category",
        "currency",
        "gross",
        "fee",
        "net",
        "created_utc",
        "available_on_utc",
    ],
    "membership": [
        "balance_transaction_id",
        "automatic_payout_id",
        "currency",
        "gross",
        "fee",
        "net",
        "created_utc",
    ],
    "movements": [
        "balance_transaction_id",
        "payout_id",
        "reporting_category",
        "currency",
        "gross",
        "fee",
        "net",
        "effective_at_utc",
    ],
    "bank": [
        "bank_line_id",
        "payout_reference",
        "currency",
        "amount",
        "booked_utc",
        "description",
    ],
    "summary": ["category", "currency", "net_amount"],
}


def decode(source, body, period_end):
    if source not in SOURCES:
        raise ContractError("Unknown source")
    if len(body) > 30_000_000:
        raise ContractError("Source file exceeds 30 MB")
    try:
        if source == "payouts":
            rows = json.loads(body)
            if not isinstance(rows, list):
                raise ContractError("Payout fixture must be a JSON array")
        else:
            reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
            if reader.fieldnames != CSV_FIELDS[source]:
                raise ContractError("Unexpected report columns/order for " + source)
            rows = list(reader)
    except (UnicodeError, json.JSONDecodeError, csv.Error) as e:
        raise ContractError("Unreadable UTF-8 source") from e
    if not rows or len(rows) > 100_000:
        raise ContractError("File must contain 1 to 100,000 rows")
    result = []
    seen = set()
    for r in rows:
        try:
            cur = currency(r["currency"])
            p = {"currency": cur}
            if source == "ledger":
                business = key(r["event_id"])
                effective = timestamp(r["occurred_utc"])
                p.update(
                    event_id=business,
                    source_id=key(r["source_id"]),
                    category=key(r["reporting_category"]),
                    gross_minor=minor(r["gross_minor"]),
                    fee_minor=minor(r["expected_fee_minor"]),
                    partner=key(r["partner"]),
                )
                p["net_minor"] = p["gross_minor"] - p["fee_minor"]
            elif source in ["activity", "membership", "movements"]:
                business = key(r["balance_transaction_id"])
                p["transaction_id"] = business
                p.update(
                    {
                        field + "_minor": major(r[field], cur)
                        for field in ["gross", "fee", "net"]
                    }
                )
                effective = timestamp(
                    r["effective_at_utc"] if source == "movements" else r["created_utc"]
                )
                if source == "activity":
                    p.update(
                        source_id=key(r["source_id"]),
                        category=key(r["reporting_category"]),
                        available_on=timestamp(r["available_on_utc"]).isoformat(),
                    )
                else:
                    p["payout_id"] = key(
                        r["payout_id"]
                        if source == "movements"
                        else r["automatic_payout_id"]
                    )
                    if source == "membership":
                        business = p["payout_id"] + ":" + business
                if source == "movements":
                    if r["reporting_category"] not in ["payout", "payout_reversal"]:
                        raise ContractError(
                            "Movement must be payout or payout_reversal"
                        )
                    p["category"] = r["reporting_category"]
            elif source == "payouts":
                if r.get("object") != "payout" or r.get("livemode") is not False:
                    raise ContractError(
                        "Only offline non-live payout fixtures are accepted"
                    )
                if not isinstance(r["automatic"], bool) or r["status"] not in [
                    "paid",
                    "pending",
                    "in_transit",
                    "failed",
                    "canceled",
                ]:
                    raise ContractError("Invalid payout status/automatic flag")
                business = key(r["id"])
                effective = datetime.fromtimestamp(minor(r["created"]), timezone.utc)
                p.update(
                    payout_id=business,
                    amount_minor=minor(r["amount"]),
                    automatic=r["automatic"],
                    status=r["status"],
                    arrival_date=datetime.fromtimestamp(
                        minor(r["arrival_date"]), timezone.utc
                    )
                    .date()
                    .isoformat(),
                )
                if p["amount_minor"] <= 0:
                    raise ContractError("Payout amount must be positive")
            elif source == "bank":
                business = key(r["bank_line_id"])
                effective = timestamp(r["booked_utc"])
                p.update(
                    bank_line_id=business,
                    payout_reference=key(r["payout_reference"])
                    if r["payout_reference"]
                    else "",
                    amount_minor=major(r["amount"], cur),
                    description=r["description"][:400],
                )
            else:
                if r["category"] not in [
                    "starting_balance",
                    "ending_balance",
                    "activity",
                    "payouts",
                ]:
                    raise ContractError("Invalid summary category")
                business = cur + ":" + r["category"]
                effective = timestamp(period_end)
                p.update(category=r["category"], net_minor=major(r["net_amount"], cur))
            if business in seen:
                raise ContractError("Duplicate source identifier within file")
            seen.add(business)
            result.append((business, effective, p))
        except (KeyError, TypeError, ValueError, OverflowError, OSError) as e:
            if isinstance(e, ContractError):
                raise
            raise ContractError("Missing/invalid source fields") from e
    return result


def payload_hash(payload, retracted=False):
    return hashlib.sha256(
        json.dumps(
            {"data": payload, "retracted": retracted},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
