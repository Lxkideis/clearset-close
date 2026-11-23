import csv, io, json
import pytest
from fastapi.testclient import TestClient
from src.api import app
from src.db import connection
from src.contracts import major, minor, timestamp, decode, ContractError
from src.engine import ingest_packet, run_close


@pytest.mark.parametrize(
    "value,currency,expected",
    [
        ("12.34", "SGD", 1234),
        ("-0.17", "USD", -17),
        ("501", "JPY", 501),
        ("90000000000.00", "SGD", 9000000000000),
    ],
)
def test_exact_minor_unit_conversion(value, currency, expected):
    assert major(value, currency) == expected


@pytest.mark.parametrize(
    "value,currency",
    [
        ("0.001", "SGD"),
        ("1.2", "JPY"),
        ("NaN", "USD"),
        ("1e2", "SGD"),
        ("90000000001", "USD"),
    ],
)
def test_no_rounding_or_non_decimal_money(value, currency):
    with pytest.raises(ContractError):
        major(value, currency)


@pytest.mark.parametrize("value", [True, 1.2, "1.00", 10**20])
def test_api_minor_units_strict(value):
    with pytest.raises(ContractError):
        minor(value)


def test_timezone_and_extreme_api_timestamp():
    with pytest.raises(ContractError):
        timestamp("2026-10-01")
    assert timestamp("2026-10-01T00:00:00+08:00").hour == 16
    body = [
        dict(
            object="payout",
            livemode=False,
            currency="sgd",
            automatic=True,
            status="paid",
            id="po_bad",
            created=9000000000000,
            arrival_date=1,
            amount=100,
        )
    ]
    with pytest.raises(ContractError):
        decode("payouts", json.dumps(body).encode(), "2026-10-21T00:00:00+08:00")


def test_invalid_file_rejects_entire_packet(sample):
    root, m = sample
    (root / "bad.csv").write_text("wrong,columns\na,b\n")
    p = json.loads(json.dumps(m["packets"][0]))
    p["files"][-1]["file"] = "bad.csv"
    with pytest.raises(ContractError):
        ingest_packet(p, root)
    with connection() as c:
        assert c.execute("SELECT count(*) n FROM import_batch").fetchone()["n"] == 0
    p["files"][-1]["file"] = "../outside.csv"
    with pytest.raises(ContractError, match="Invalid fixture"):
        ingest_packet(p, root)


def test_api_evidence_permissions_filters_and_non_financial_notes(staged):
    _, m, r = staged
    client = TestClient(app)
    rid = r[0]["id"]
    assert client.get("/health/ready").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert (
        client.post(
            f"/api/runs/{rid}/certify", json={"note": "Review is complete."}
        ).status_code
        == 401
    )
    headers = {"Authorization": "Bearer demo-analyst-token"}
    assert (
        client.post(
            f"/api/runs/{rid}/certify",
            headers=headers,
            json={"note": "Review is complete."},
        ).status_code
        == 403
    )
    item = m["initial_named_findings"][0]
    evidence = client.get(
        f"/api/runs/{rid}/evidence",
        params={"kind": item["kind"], "entity_key": item["entity_key"]},
    ).json()
    assert (
        len(evidence["source_records"]) >= 2
        and evidence["source_records"][0]["file_sha256"]
    )
    note = client.post(
        f"/api/runs/{rid}/notes",
        headers=headers,
        json={
            **{k: item[k] for k in ["kind", "entity_key"]},
            "note": "Investigate original source components before correcting.",
        },
    )
    assert note.status_code == 200 and note.json()["financial_results_changed"] is False
    assert (
        client.get(f"/api/runs/{rid}").json()["run"]["stats"]["severity_counts"]
        == r[0]["stats"]["severity_counts"]
    )
    queue = client.get(
        f"/api/runs/{rid}/queue", params={"currency": "SGD", "limit": 2}
    ).json()
    assert len(queue["rows"]) <= 2 and all(
        x["currency"] == "SGD" for x in queue["rows"]
    )
    body = client.get(
        f"/api/runs/{rid}/export.csv", params={"currency": "SGD", "kind": "transaction"}
    ).text
    rows = list(csv.DictReader(io.StringIO(body)))
    assert rows and all(
        x["currency"] == "SGD" and x["kind"] == "transaction" for x in rows
    )
    detail = client.get(f"/api/runs/{r[2]['id']}").json()
    assert isinstance(detail["balance"][0]["opening_minor"], str)
    assert client.get(f"/api/runs/{rid}/queue?currency=EUR").status_code == 422
    assert client.get(f"/api/runs/{rid}/queue?limit=10001").status_code == 422


def test_aging_evidence_has_original_source(staged):
    client = TestClient(app)
    rid = staged[2][2]["id"]
    q = client.get(f"/api/runs/{rid}/queue?kind=aging&severity=all").json()
    assert q["rows"]
    e = client.get(
        f"/api/runs/{rid}/evidence",
        params={"kind": "aging", "entity_key": q["rows"][0]["entity_key"]},
    ).json()
    assert len(e["source_records"]) == 1


def test_missing_sources_block_certification(sample):
    _, m = sample
    run = run_close(m["period_start"], m["period_end"], m["knowledge_cutoffs"][0])
    assert (
        not run["stats"]["ready_for_review"]
        and run["stats"]["by_kind"]["coverage"]["missing_source"] == 7
    )


def test_generated_memo_reads_actual_snapshot_and_retains_limits(staged):
    client = TestClient(app)
    rid = staged[2][0]["id"]
    response = client.get(f"/api/runs/{rid}/memo.html")
    assert response.status_code == 200
    assert "blocking control findings require source correction" in response.text
    assert rid in response.text and "No FX conversion" in response.text
    final = client.get(f"/api/runs/{staged[2][2]['id']}/memo.html").text
    assert "no blocking control findings remain" in final
