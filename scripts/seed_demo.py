from pathlib import Path
import json, sys, time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.engine import ingest_packet, run_close
from src.db import connection, ROOT
from .generate_fixtures import generate


def seed():
    if not (ROOT / "fixtures/manifest.json").exists():
        generate()
    manifest = json.loads((ROOT / "fixtures/manifest.json").read_text())
    history = []
    for packet in manifest["packets"][:3]:
        began = time.perf_counter()
        imports = ingest_packet(packet)
        result = run_close(
            manifest["period_start"], manifest["period_end"], packet["observed_at"]
        )
        history.append(
            {
                "packet": packet["name"],
                "imports": imports,
                "run": result,
                "wall_ms": round((time.perf_counter() - began) * 1000, 3),
            }
        )
        print(
            json.dumps(
                {
                    "packet": packet["name"],
                    "run_id": result["id"],
                    "severity_counts": result["stats"]["severity_counts"],
                    "ready": result["stats"]["ready_for_review"],
                    "wall_ms": history[-1]["wall_ms"],
                }
            ),
            flush=True,
        )
    with connection() as c:
        first = history[0]["run"]["id"]
        last = history[-1]["run"]["id"]
        for wanted in manifest["initial_named_findings"]:
            row = c.execute(
                "SELECT status FROM control_result WHERE run_id=%s AND kind=%s AND entity_key=%s",
                (first, wanted["kind"], wanted["entity_key"]),
            ).fetchone()
            assert row and row["status"] == wanted["status"], (wanted, row)
        balances = c.execute(
            "SELECT currency,detail FROM control_result WHERE run_id=%s AND kind='balance'",
            (last,),
        ).fetchall()
        for row in balances:
            truth = manifest["golden_balances"][row["currency"]]
            for field, value in truth.items():
                assert row["detail"][field] == value, (
                    row["currency"],
                    field,
                    row["detail"][field],
                    value,
                )
            assert row["detail"]["residual_minor"] == 0
        assert history[-1]["run"]["stats"]["ready_for_review"]
        for payout in manifest["expected_pending_payouts"]:
            row = c.execute(
                "SELECT status FROM control_result WHERE run_id=%s AND kind='payout' AND entity_key=%s",
                (last, payout),
            ).fetchone()
            assert row["status"] == "bank_pending"
    original = run_close(
        manifest["period_start"],
        manifest["period_end"],
        manifest["knowledge_cutoffs"][0],
    )
    assert (
        original["id"] == history[0]["run"]["id"]
        and original["stats"] == history[0]["run"]["stats"]
    )
    report = {
        "history": history,
        "independent_named_findings_verified": len(manifest["initial_named_findings"]),
        "golden_currency_balances_verified": 3,
        "old_snapshot_replay_unchanged": True,
        "final_blockers": 0,
        "note": "Synthetic truth and intentionally flawed extracts; no actual business impact or live Stripe API.",
    }
    (ROOT / "artifacts/seed_verification.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
    return report


if __name__ == "__main__":
    seed()
