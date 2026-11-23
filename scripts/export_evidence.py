"""Rebuild reviewable artifacts and measure full SQL close runs on the local fixture, not ROI."""

from datetime import timedelta
import csv, json, platform, statistics, time
from src.db import ROOT, connection
from src.engine import serial, run_close, timestamp, prepare_views
from src.reporting import close_memo
from .seed_demo import seed


def export():
    evidence = seed()
    m = json.loads((ROOT / "fixtures/manifest.json").read_text())
    out = ROOT / "artifacts"
    out.mkdir(exist_ok=True)
    for item in evidence["history"]:
        rid = item["run"]["id"]
        name = item["packet"]
        (out / f"{name}_finance_memo.html").write_text(
            close_memo(rid), encoding="utf-8"
        )
        with connection() as c:
            rows = c.execute(
                "SELECT kind,entity_key,currency,status,severity,detail FROM control_result WHERE run_id=%s ORDER BY kind,entity_key",
                (rid,),
            ).fetchall()
            run = c.execute("SELECT * FROM close_run WHERE id=%s", (rid,)).fetchone()
        (out / f"{name}_run.json").write_text(
            json.dumps(serial(run), indent=2), encoding="utf-8"
        )
        with (out / f"{name}_controls.csv").open(
            "w", newline="", encoding="utf-8-sig"
        ) as f:
            w = csv.writer(f)
            w.writerow(
                ["kind", "entity_key", "currency", "status", "severity", "detail_json"]
            )
            for row in rows:
                w.writerow(
                    [
                        json.dumps(serial(v), ensure_ascii=False)
                        if isinstance(v, dict)
                        else v
                        for v in row.values()
                    ]
                )
    samples = []
    runs = []
    with connection() as c:
        anchor = c.execute(
            "SELECT max(knowledge_cutoff) value FROM close_run"
        ).fetchone()["value"]
    anchor = max(timestamp(m["knowledge_cutoffs"][2]), anchor)
    for i in range(8):
        cutoff = anchor + timedelta(seconds=i + 1)
        began = time.perf_counter()
        r = run_close(m["period_start"], m["period_end"], cutoff)
        elapsed = (time.perf_counter() - began) * 1000
        if i:
            samples.append(round(elapsed, 3))
            runs.append({"id": r["id"], "replayed": r["replayed"]})
    params = {
        "start": timestamp(m["period_start"]),
        "end": timestamp(m["period_end"]),
        "cutoff": timestamp(m["knowledge_cutoffs"][2]),
    }
    with connection() as c:
        prepare_views(c, params)
        plan = c.execute(
            "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) "
            + (ROOT / "sql/020_transactions.sql").read_text(),
            params,
        ).fetchone()["QUERY PLAN"]
        version = c.execute("SELECT version() version").fetchone()["version"]
    (out / "transaction_query_plan.json").write_text(
        json.dumps(serial(plan), indent=2), encoding="utf-8"
    )
    result = {
        "measurement": "Full financial-control snapshot including persistence; ingestion excluded; one warm-up plus seven different knowledge-cutoff snapshots",
        "wall_ms": samples,
        "median_ms": round(statistics.median(samples), 3),
        "min_ms": min(samples),
        "max_ms": max(samples),
        "all_measured_runs_new": all(not x["replayed"] for x in runs),
        "runs": runs,
        "bulk_charges": m["bulk_charge_count"],
        "golden_source_counts": m["golden_source_counts"],
        "python": platform.python_version(),
        "platform": platform.platform(),
        "postgresql": version,
        "limits": "Small local synthetic fixture. No production throughput, business time savings, SLA, load or scale claim.",
    }
    # A later rerun may replay prior benchmark cutoffs; label that fact explicitly.
    (out / "benchmark.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                k: result[k]
                for k in ["median_ms", "all_measured_runs_new", "bulk_charges"]
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    export()
