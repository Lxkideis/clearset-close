"""Independent synthetic economic truth -> faulty reports -> explicit corrections."""

from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from copy import deepcopy
import csv, hashlib, json, random, sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.contracts import CSV_FIELDS, EXPONENT

ROOT = Path(__file__).resolve().parents[1]
START = "2026-10-01T00:00:00+08:00"
END = "2026-10-21T00:00:00+08:00"
KNOWN = [
    "2026-10-21T02:00:00+00:00",
    "2026-10-21T03:00:00+00:00",
    "2026-10-21T04:00:00+00:00",
]


def stamp(day, hour=4):
    return datetime(2026, 10, day, hour, tzinfo=timezone.utc)


def major(value, cur):
    sign = "-" if value < 0 else ""
    value = abs(value)
    exp = EXPONENT[cur]
    return sign + (f"{value // 100}.{value % 100:02d}" if exp == 2 else str(value))


def number(value, cur):
    return int(str(value).replace(".", "")) if EXPONENT[cur] == 2 else int(value)


def generate(root=None, count=3000):
    root = Path(root or ROOT / "fixtures")
    root.mkdir(parents=True, exist_ok=True)
    rng = random.Random(2027)
    if count < 30:
        raise ValueError("At least 30 bulk events for the named scenarios")
    golden = {
        s: []
        for s in [
            "ledger",
            "activity",
            "membership",
            "payouts",
            "movements",
            "bank",
            "summary",
        ]
    }

    def event(
        name,
        cur,
        gross,
        fee,
        when,
        category="charge",
        payout=None,
        source=None,
        available=None,
        partner="Partner_A",
    ):
        source = source or "ch_" + name
        transaction = "txn_" + name
        ledger = {
            "event_id": "evt_" + name,
            "source_id": source,
            "reporting_category": category,
            "currency": cur,
            "gross_minor": gross,
            "expected_fee_minor": fee,
            "occurred_utc": when.isoformat(),
            "partner": partner,
        }
        activity = {
            "balance_transaction_id": transaction,
            "source_id": source,
            "reporting_category": category,
            "currency": cur,
            "gross": major(gross, cur),
            "fee": major(fee, cur),
            "net": major(gross - fee, cur),
            "created_utc": when.isoformat(),
            "available_on_utc": (available or when + timedelta(days=1)).isoformat(),
        }
        golden["ledger"].append(ledger)
        golden["activity"].append(activity)
        if payout:
            golden["membership"].append(
                {
                    k: activity[k]
                    for k in [
                        "balance_transaction_id",
                        "currency",
                        "gross",
                        "fee",
                        "net",
                        "created_utc",
                    ]
                }
                | {"automatic_payout_id": payout}
            )
        return transaction

    for i in range(count):
        cur = ["SGD", "USD", "JPY"][i % 3]
        day = 2 + i % 14
        gross = rng.randint(2000, 500000)
        fee = (gross * (210 if i % 2 == 0 else 250) + 5000) // 10000 + (
            30 if cur != "JPY" else 20
        )
        payout = f"po_{cur}_{day + 2:02d}"
        name = f"{i:06d}"
        event(
            name,
            cur,
            gross,
            fee,
            stamp(day),
            payout=payout,
            partner="Partner_A" if i % 2 == 0 else "Partner_B",
        )
        if i % 41 == 0:
            event(
                "refund_" + name,
                cur,
                -gross // 3,
                0,
                stamp(day, 5),
                "refund",
                payout,
                "re_" + name,
            )
        if i % 73 == 0:
            event(
                "dispute_" + name,
                cur,
                -gross,
                1500,
                stamp(day, 6),
                "dispute",
                payout,
                "dp_" + name,
            )
            if i % 146 == 0:
                event(
                    "reversal_" + name,
                    cur,
                    gross,
                    -1500,
                    stamp(day, 7),
                    "dispute_reversal",
                    payout,
                    "dp_" + name,
                )
    prior = datetime(2026, 9, 29, 4, tzinfo=timezone.utc)
    event("prior_period", "SGD", 5000, 145, prior, payout="po_SGD_01")
    event(
        "old_retained",
        "SGD",
        8000,
        200,
        prior - timedelta(days=2),
        available=prior - timedelta(days=1),
    )
    event("future_available", "USD", 9000, 250, stamp(20, 10), available=stamp(25))
    event(
        "cutoff_before",
        "SGD",
        11000,
        300,
        datetime(2026, 10, 20, 15, 59, 59, tzinfo=timezone.utc),
        available=stamp(22),
    )
    event(
        "cutoff_exact",
        "SGD",
        12000,
        320,
        datetime(2026, 10, 20, 16, tzinfo=timezone.utc),
        available=stamp(22),
    )
    event(
        "pending_batch",
        "SGD",
        7000,
        200,
        stamp(20, 12),
        payout="po_pending",
        available=stamp(21),
    )
    event("soft_pending", "JPY", 5100, 100, stamp(18), payout="po_soft_pending")
    for label in ["a", "b"]:
        event(
            "candidate_" + label,
            "USD",
            5030,
            30,
            stamp(10),
            payout="po_candidate_" + label,
        )
    batches = defaultdict(lambda: {"currency": None, "net": 0})
    for row in golden["membership"]:
        info = batches[row["automatic_payout_id"]]
        info["currency"] = row["currency"]
        info["net"] += number(row["net"], row["currency"])

    def payout(pid, cur, amount, day, status="paid", automatic=True):
        arrive = stamp(day, 0)
        created = arrive - timedelta(days=1)
        if pid == "po_pending":
            created = stamp(20, 13)
        golden["payouts"].append(
            {
                "id": pid,
                "object": "payout",
                "amount": amount,
                "currency": cur.lower(),
                "created": int(created.timestamp()),
                "arrival_date": int(arrive.timestamp()),
                "automatic": automatic,
                "status": status,
                "livemode": False,
            }
        )
        golden["movements"].append(
            {
                "balance_transaction_id": "txn_out_" + pid,
                "payout_id": pid,
                "reporting_category": "payout",
                "currency": cur,
                "gross": major(-amount, cur),
                "fee": major(0, cur),
                "net": major(-amount, cur),
                "effective_at_utc": arrive.isoformat(),
            }
        )
        if status == "paid":
            bankdate = stamp(day, 10) if pid != "po_soft_pending" else stamp(21, 1)
            golden["bank"].append(
                {
                    "bank_line_id": "bank_" + pid,
                    "payout_reference": pid,
                    "currency": cur,
                    "amount": major(amount, cur),
                    "booked_utc": bankdate.isoformat(),
                    "description": "Synthetic payout credit",
                }
            )

    for pid, info in sorted(batches.items()):
        day = (
            22
            if pid == "po_pending"
            else 20
            if pid == "po_soft_pending"
            else 12
            if pid.startswith("po_candidate")
            else int(pid[-2:])
        )
        if info["net"] <= 0:
            raise ValueError("Synthetic payout batch is not positive")
        payout(
            pid,
            info["currency"],
            info["net"],
            day,
            "in_transit" if pid == "po_pending" else "paid",
        )
    payout("po_manual", "SGD", 10000, 18, automatic=False)
    payout("po_failed", "SGD", 7000, 15, status="failed", automatic=False)
    golden["movements"].append(
        {
            "balance_transaction_id": "txn_return_po_failed",
            "payout_id": "po_failed",
            "reporting_category": "payout_reversal",
            "currency": "SGD",
            "gross": "70.00",
            "fee": "0.00",
            "net": "70.00",
            "effective_at_utc": stamp(16).isoformat(),
        }
    )
    start = datetime.fromisoformat(START)
    end = datetime.fromisoformat(END)
    truth = {}
    for cur in EXPONENT:
        before = sum(
            number(r["net"], cur)
            for r in golden["activity"]
            if r["currency"] == cur and datetime.fromisoformat(r["created_utc"]) < start
        )
        opening = 1_000_000 + before
        activity = sum(
            number(r["net"], cur)
            for r in golden["activity"]
            if r["currency"] == cur
            and start <= datetime.fromisoformat(r["created_utc"]) < end
        )
        movement = sum(
            number(r["net"], cur)
            for r in golden["movements"]
            if r["currency"] == cur
            and start <= datetime.fromisoformat(r["effective_at_utc"]) < end
        )
        ending = opening + activity + movement
        truth[cur] = {
            "opening_minor": opening,
            "activity_minor": activity,
            "movement_minor": movement,
            "ending_minor": ending,
        }
        for category, value in [
            ("starting_balance", opening),
            ("activity", activity),
            ("payouts", -movement),
            ("ending_balance", ending),
        ]:
            golden["summary"].append(
                {"category": category, "currency": cur, "net_amount": major(value, cur)}
            )
    initial = deepcopy(golden)
    fixes = defaultdict(list)
    late = defaultdict(list)
    retractions = defaultdict(list)
    bank_fixes = defaultdict(list)
    bank_late = defaultdict(list)
    bank_retractions = defaultdict(list)

    def locate(source, field, value):
        return next(r for r in initial[source] if r[field] == value)

    def true_row(source, field, value):
        return deepcopy(next(r for r in golden[source] if r[field] == value))

    def amend_activity(i, edit):
        row = locate("activity", "balance_transaction_id", f"txn_{i:06d}")
        fixes["activity"].append(deepcopy(row))
        edit(row)

    amend_activity(
        0,
        lambda r: r.update(
            gross=major(number(r["gross"], r["currency"]) + 5, r["currency"]),
            fee=major(number(r["fee"], r["currency"]) + 5, r["currency"]),
        ),
    )

    def bad_fee(r):
        r["fee"] = major(number(r["fee"], r["currency"]) + 10, r["currency"])
        r["net"] = major(
            number(r["gross"], r["currency"]) - number(r["fee"], r["currency"]),
            r["currency"],
        )

    amend_activity(1, bad_fee)
    row = locate("activity", "balance_transaction_id", "txn_000002")
    initial["activity"].remove(row)
    late["activity"].append(row)
    row = locate("ledger", "event_id", "evt_000003")
    initial["ledger"].remove(row)
    late["ledger"].append(row)
    duplicate = deepcopy(locate("activity", "balance_transaction_id", "txn_000004"))
    duplicate["balance_transaction_id"] = "txn_business_duplicate"
    initial["activity"].append(duplicate)
    retractions["activity"].append(deepcopy(duplicate))
    amend_activity(5, lambda r: r.update(currency="USD"))
    amend_activity(
        6,
        lambda r: r.update(
            net=major(number(r["net"], r["currency"]) + 1, r["currency"])
        ),
    )
    row = locate("membership", "balance_transaction_id", "txn_000007")
    initial["membership"].remove(row)
    late["membership"].append(row)
    orphan = deepcopy(locate("membership", "balance_transaction_id", "txn_000008"))
    orphan["balance_transaction_id"] = "txn_missing_source"
    initial["membership"].append(orphan)
    retractions["membership"].append(deepcopy(orphan))
    row = locate("payouts", "id", "po_JPY_12")
    fixes["payouts"].append(deepcopy(row))
    row["amount"] += 1
    for bid in ["bank_po_SGD_13"]:
        row = locate("bank", "bank_line_id", bid)
        initial["bank"].remove(row)
        bank_late["bank"].append(row)
    row = locate("bank", "bank_line_id", "bank_po_candidate_a")
    bank_fixes["bank"].append(deepcopy(row))
    row["payout_reference"] = "unmapped_reference"
    duplicate = deepcopy(locate("bank", "bank_line_id", "bank_po_USD_14"))
    duplicate["bank_line_id"] = "bank_duplicate"
    initial["bank"].append(duplicate)
    bank_retractions["bank"].append(deepcopy(duplicate))
    row = locate("bank", "bank_line_id", "bank_po_JPY_15")
    bank_fixes["bank"].append(deepcopy(row))
    row["amount"] = major(number(row["amount"], row["currency"]) - 7, row["currency"])
    row = next(
        r
        for r in initial["summary"]
        if r["currency"] == "USD" and r["category"] == "ending_balance"
    )
    bank_fixes["summary"].append(deepcopy(row))
    row["net_amount"] = major(number(row["net_amount"], "USD") + 1, "USD")
    for tid in ["txn_return_po_failed", "txn_out_po_SGD_16"]:
        row = locate("movements", "balance_transaction_id", tid)
        initial["movements"].remove(row)
        late["movements"].append(row)
    packets = []

    def file(directory, source, rows, label="data"):
        extension = "json" if source == "payouts" else "csv"
        path = root / directory / f"{source}_{label}.{extension}"
        path.parent.mkdir(exist_ok=True)
        if source == "payouts":
            path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
        else:
            with path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=CSV_FIELDS[source])
                writer.writeheader()
                writer.writerows(rows)
        return path.relative_to(root).as_posix()

    for name, known, groups in [
        ("initial", KNOWN[0], [(initial, 1, False, "data")]),
        (
            "repair_data",
            KNOWN[1],
            [
                (fixes, 2, False, "correct"),
                (late, 1, False, "late"),
                (retractions, 2, True, "retract"),
            ],
        ),
        (
            "repair_bank",
            KNOWN[2],
            [
                (bank_fixes, 2, False, "correct"),
                (bank_late, 1, False, "late"),
                (bank_retractions, 2, True, "retract"),
            ],
        ),
        ("golden", KNOWN[2], [(golden, 1, False, "data")]),
    ]:
        files = []
        for data, rev, retracted, label in groups:
            for source, rows in data.items():
                if rows:
                    files.append(
                        {
                            "source": source,
                            "file": file(name, source, rows, label),
                            "revision": rev,
                            "retracted": retracted,
                        }
                    )
        packets.append(
            {
                "name": name,
                "observed_at": known,
                "period_start": START,
                "period_end": END,
                "files": files,
            }
        )
    expected = [
        ("transaction", "ch_000000:charge", "offsetting_gross_fee"),
        ("transaction", "ch_000001:charge", "fee_mismatch"),
        ("transaction", "ch_000002:charge", "missing_processor"),
        ("transaction", "ch_000003:charge", "missing_ledger"),
        ("transaction", "ch_000004:charge", "duplicate_processor_key"),
        ("transaction", "ch_000005:charge", "currency_mismatch"),
        ("transaction", "ch_000006:charge", "invalid_source_arithmetic"),
        ("payout", "po_SGD_13", "bank_missing"),
        ("payout", "po_USD_14", "ambiguous_bank_reference"),
        ("payout", "po_JPY_15", "bank_amount_mismatch"),
        ("payout", "po_SGD_16", "payout_movement_mismatch"),
        ("bank", "bank_po_candidate_a", "unmapped_bank_credit"),
        ("balance", "USD", "summary_identity_mismatch"),
    ]
    manifest = {
        "synthetic": True,
        "seed": 2027,
        "bulk_charge_count": count,
        "period_start": START,
        "period_end": END,
        "knowledge_cutoffs": KNOWN,
        "packets": packets,
        "golden_source_counts": {k: len(v) for k, v in golden.items()},
        "golden_balances": truth,
        "initial_named_findings": [
            {"kind": k, "entity_key": key, "status": status}
            for k, key, status in expected
        ],
        "expected_final_blockers": 0,
        "expected_pending_payouts": ["po_pending", "po_soft_pending"],
        "fee_schedule_note": "Fictional Partner_A/B expected fees; not Stripe pricing.",
        "source_note": "Offline synthetic report subsets plus lab observation/revision envelopes; no API call or real bank/customer data.",
    }
    manifest["sha256"] = {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


if __name__ == "__main__":
    result = generate()
    print(
        json.dumps(
            {
                k: result[k]
                for k in [
                    "bulk_charge_count",
                    "golden_source_counts",
                    "golden_balances",
                ]
            },
            indent=2,
        )
    )
