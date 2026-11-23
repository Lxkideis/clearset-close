import json
from urllib.parse import urlparse
import psycopg, pytest
from src.db import ROOT, connection
from src.engine import ingest_packet, run_close
from scripts.generate_fixtures import generate


@pytest.fixture(autouse=True)
def isolated_database(monkeypatch):
    state = json.loads((ROOT / "_runtime/state.json").read_text())
    target = urlparse(state["test_owner_url"])
    assert target.hostname == "127.0.0.1" and target.path == "/stripeclose_test", (
        "Refuse cleanup outside isolated test database"
    )
    monkeypatch.setenv("STRIPECLOSE_DATABASE_URL", state["test_url"])
    with psycopg.connect(state["test_owner_url"]) as c:
        c.execute(
            "TRUNCATE closeops.certification,closeops.case_note,closeops.control_result,closeops.close_run,closeops.observation,closeops.import_batch CASCADE"
        )
    yield


@pytest.fixture
def sample(tmp_path):
    generate(tmp_path, count=60)
    return tmp_path, json.loads((tmp_path / "manifest.json").read_text())


@pytest.fixture
def staged(sample):
    root, m = sample
    runs = []
    for p in m["packets"][:3]:
        ingest_packet(p, root)
        runs.append(run_close(m["period_start"], m["period_end"], p["observed_at"]))
    return root, m, runs


def result(run, kind, key):
    with connection() as c:
        return c.execute(
            "SELECT * FROM control_result WHERE run_id=%s AND kind=%s AND entity_key=%s",
            (run["id"], kind, key),
        ).fetchone()
