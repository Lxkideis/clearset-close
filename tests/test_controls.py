from datetime import date
import json
from concurrent.futures import ThreadPoolExecutor
import pytest, psycopg
from src.db import ROOT, connection
from src.engine import (
    ingest_packet,
    run_close,
    certify,
    business_deadline,
    Conflict,
    Permission,
)
from src.contracts import ContractError
from conftest import result


def test_independent_truth_and_each_named_failure(staged):
    _, m, runs = staged
    for item in m["initial_named_findings"]:
        assert (
            result(runs[0], item["kind"], item["entity_key"])["status"]
            == item["status"]
        )
    assert (
        runs[0]["stats"]["severity_counts"]["blocker"]
        > runs[1]["stats"]["severity_counts"]["blocker"]
        > 0
    )
    assert runs[2]["stats"]["ready_for_review"]
    for cur, truth in m["golden_balances"].items():
        detail = result(runs[2], "balance", cur)["detail"]
        for key, value in truth.items():
            assert detail[key] == value
        assert detail["residual_minor"] == 0


def test_offsetting_components_and_cross_currency_are_not_net_matches(staged):
    _, m, r = staged
    offset = next(
        x for x in m["initial_named_findings"] if x["status"] == "offsetting_gross_fee"
    )
    d = result(r[0], offset["kind"], offset["entity_key"])["detail"]
    assert (
        d["delta_minor"] == 0 and d["ledger_gross_minor"] != d["activity_gross_minor"]
    )
    cross = next(
        x for x in m["initial_named_findings"] if x["status"] == "currency_mismatch"
    )
    assert (
        result(r[0], cross["kind"], cross["entity_key"])["detail"]["delta_minor"]
        is None
    )


def test_old_cutoff_and_retracted_records_are_replayable(staged):
    _, m, r = staged
    replay = run_close(m["period_start"], m["period_end"], m["knowledge_cutoffs"][0])
    assert (
        replay["replayed"]
        and replay["id"] == r[0]["id"]
        and replay["stats"] == r[0]["stats"]
    )
    with connection() as c:
        assert (
            c.execute("SELECT count(*) n FROM observation WHERE retracted").fetchone()[
                "n"
            ]
            >= 3
        )
    assert not r[2]["stats"]["by_kind"]["transaction"].get("duplicate_processor_key")


def test_pending_failed_manual_and_bank_ambiguity(staged):
    _, m, r = staged
    for key in m["expected_pending_payouts"]:
        assert result(r[2], "payout", key)["status"] == "bank_pending"
    with connection() as c:
        payouts = c.execute(
            "SELECT detail FROM control_result WHERE run_id=%s AND kind='payout'",
            (r[2]["id"],),
        ).fetchall()
    failed = [x["detail"] for x in payouts if x["detail"]["payout_status"] == "failed"]
    manual = [x["detail"] for x in payouts if not x["detail"]["automatic"]]
    assert failed and all(
        x["expected_movement_minor"] == 0 and x["bank_status"] == "not_expected"
        for x in failed
    )
    assert manual and all(x["batch_status"] == "not_applicable" for x in manual)
    bank = next(x for x in m["initial_named_findings"] if x["kind"] == "bank")
    assert (
        len(result(r[0], "bank", bank["entity_key"])["detail"]["candidate_payouts"])
        == 2
    )


def test_weighted_charge_only_fee_rates_and_lookback(staged):
    _, _, r = staged
    with connection() as c:
        costs = c.execute(
            "SELECT detail FROM control_result WHERE run_id=%s AND kind='cost'",
            (r[2]["id"],),
        ).fetchall()
        categories = c.execute(
            "SELECT DISTINCT detail->>'category' category FROM control_result WHERE run_id=%s AND kind='transaction'",
            (r[2]["id"],),
        ).fetchall()
        prior = c.execute(
            "SELECT status FROM control_result WHERE run_id=%s AND kind='membership' AND entity_key LIKE '%%prior%%'",
            (r[2]["id"],),
        ).fetchall()
    assert {"refund", "dispute"}.issubset({x["category"] for x in categories})
    assert prior and all(x["status"] == "matched" for x in prior)
    for item in costs:
        d = item["detail"]
        assert (
            abs(
                float(d["observed_fee_bps"])
                - 10000 * d["observed_fees_minor"] / d["charge_gross_minor"]
            )
            <= 0.0051
        )
        assert d["variance_minor"] == 0


def test_whole_packet_rolls_back_on_revision_conflict(sample):
    root, m = sample
    ingest_packet(m["packets"][0], root)
    with connection() as c:
        before = c.execute("SELECT count(*) n FROM observation").fetchone()["n"]
    bad = json.loads(json.dumps(m["packets"][1]))
    bad["files"] = bad["files"][:2]
    bad["files"][1]["revision"] = 1
    with pytest.raises(ContractError, match="Conflicting"):
        ingest_packet(bad, root)
    with connection() as c:
        assert c.execute("SELECT count(*) n FROM observation").fetchone()["n"] == before


def test_concurrent_import_and_snapshot_idempotency(sample):
    root, m = sample
    with ThreadPoolExecutor(2) as pool:
        imports = list(
            pool.map(lambda _: ingest_packet(m["packets"][0], root), range(2))
        )
    assert sum(x["replayed"] for items in imports for x in items) == 7
    with ThreadPoolExecutor(2) as pool:
        runs = list(
            pool.map(
                lambda _: run_close(
                    m["period_start"], m["period_end"], m["knowledge_cutoffs"][0]
                ),
                range(2),
            )
        )
    assert runs[0]["id"] == runs[1]["id"] and sum(x["replayed"] for x in runs) == 1


def test_append_only_evidence_and_completed_snapshots(staged):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with connection() as c:
            c.execute("UPDATE observation SET payload='{}'::jsonb")
    state = json.loads((ROOT / "_runtime/state.json").read_text())
    with pytest.raises(psycopg.errors.RaiseException):
        with psycopg.connect(state["test_owner_url"]) as c:
            c.execute("UPDATE closeops.observation SET payload='{}'::jsonb")
    with pytest.raises(psycopg.errors.RaiseException):
        with connection() as c:
            c.execute("UPDATE close_run SET stats='{}'::jsonb")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with connection() as c:
            c.execute("TRUNCATE observation")


def test_reviewer_separation_and_blocking_controls(staged):
    _, _, r = staged
    with pytest.raises(Permission):
        certify(r[2]["id"], "demo-analyst", "reviewer", "Reviewed all evidence.")
    with pytest.raises(Permission):
        certify(r[2]["id"], "demo-reviewer", "analyst", "Reviewed all evidence.")
    with pytest.raises(Conflict):
        certify(r[0]["id"], "demo-reviewer", "reviewer", "Reviewed all evidence.")
    signed = certify(
        r[2]["id"],
        "demo-reviewer",
        "reviewer",
        "Reviewed bank timing and remaining advisory.",
    )
    assert signed["run_id"] == r[2]["id"]
    with pytest.raises(Conflict, match="Already"):
        certify(r[2]["id"], "demo-reviewer", "reviewer", "Reviewed all evidence.")


def test_business_deadline_weekends_and_explicit_holiday():
    assert business_deadline(date(2026, 10, 16), 2, []) == date(2026, 10, 20)
    assert business_deadline(date(2026, 10, 16), 2, ["2026-10-19"]) == date(
        2026, 10, 21
    )


def test_new_evidence_prevents_old_clean_snapshot_certification(sample):
    root, m = sample
    p = json.loads(json.dumps(m["packets"][3]))
    p["observed_at"] = m["knowledge_cutoffs"][0]
    ingest_packet(p, root)
    r = run_close(m["period_start"], m["period_end"], p["observed_at"])
    assert r["stats"]["ready_for_review"]
    p["observed_at"] = m["knowledge_cutoffs"][2]
    p["files"] = p["files"][:1]
    p["files"][0]["revision"] = 2
    ingest_packet(p, root)
    with pytest.raises(Conflict, match="Newer source"):
        certify(
            r["id"], "demo-reviewer", "reviewer", "Reviewed the original clean data."
        )


def test_sg_cutoff_excludes_boundary_and_aging_is_not_loss(staged):
    _, _, runs = staged
    with connection() as c:
        keys = c.execute(
            "SELECT entity_key FROM control_result WHERE run_id=%s AND kind='transaction' AND entity_key LIKE '%%cutoff%%'",
            (runs[2]["id"],),
        ).fetchall()
        aged = c.execute(
            "SELECT detail FROM control_result WHERE run_id=%s AND kind='aging'",
            (runs[2]["id"],),
        ).fetchall()
    assert len(keys) == 1 and "before" in keys[0]["entity_key"]
    assert any(x["detail"]["status"] == "not_yet_available" for x in aged)


def test_nonzero_payout_fee_is_explicitly_unsupported(sample):
    import csv
    from decimal import Decimal

    root, m = sample
    p = m["packets"][3]
    ingest_packet(p, root)
    source = root / "golden/movements_data.csv"
    with source.open(newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        row = next(reader)
    fee = Decimal("1") if row["currency"] == "JPY" else Decimal("0.01")
    row["fee"] = str(fee)
    row["net"] = str(Decimal(row["gross"]) - fee)
    with (root / "fee.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerow(row)
    packet = {**p, "files": [{"source": "movements", "file": "fee.csv", "revision": 2}]}
    ingest_packet(packet, root)
    r = run_close(m["period_start"], m["period_end"], p["observed_at"])
    assert (
        result(r, "payout", row["payout_id"])["detail"]["movement_status"]
        == "unsupported_payout_fee"
    )
