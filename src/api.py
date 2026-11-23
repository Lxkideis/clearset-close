from datetime import datetime
from typing import Literal
from uuid import UUID, uuid4
import csv, io, json, os, secrets
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from .db import ROOT, connection
from .engine import (
    run_close,
    ingest_packet,
    certify,
    serial,
    engine_hash,
    Conflict,
    Permission,
    POLICY,
)
from .contracts import ContractError

app = FastAPI(
    title="Clearset Close",
    version="1.0.0",
    description="Offline synthetic financial controls; not affiliated with Stripe. No money movement.",
)
app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
auth = HTTPBearer(auto_error=False)


def operator(credential: HTTPAuthorizationCredentials | None = Depends(auth)):
    if credential:
        for role in ["analyst", "reviewer"]:
            if secrets.compare_digest(
                credential.credentials,
                os.getenv(
                    "STRIPECLOSE_" + role.upper() + "_TOKEN", "demo-" + role + "-token"
                ),
            ):
                return {"role": role, "actor": "demo-" + role}
    raise HTTPException(401, "Use the local demo operator token")


def protected(call, *args, **kwargs):
    try:
        return call(*args, **kwargs)
    except Permission as e:
        raise HTTPException(403, str(e)) from e
    except Conflict as e:
        raise HTTPException(409, str(e)) from e
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except (ContractError, ValueError) as e:
        raise HTTPException(422, str(e)) from e


def money_strings(value):
    if isinstance(value, dict):
        return {
            k: str(v)
            if k.endswith("_minor") and isinstance(v, (int, float))
            else money_strings(v)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [money_strings(v) for v in value]
    return serial(value)


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(ROOT / "web/index.html")


@app.get("/health/live")
def live():
    return {"status": "live"}


@app.get("/health/ready")
def ready():
    try:
        with connection() as c:
            c.execute("SELECT 1")
        return {"status": "ready"}
    except Exception:
        return Response("Database unavailable", 503)


@app.get("/api/scenario")
def scenario():
    data = json.loads((ROOT / "fixtures/manifest.json").read_text())
    return {
        k: data[k]
        for k in [
            "period_start",
            "period_end",
            "knowledge_cutoffs",
            "golden_source_counts",
            "initial_named_findings",
            "source_note",
        ]
    }


@app.get("/api/runs")
def runs():
    with connection() as c:
        rows = c.execute(
            """SELECT r.*,s.reviewer,s.created_at certified_at FROM close_run r
          LEFT JOIN certification s ON s.run_id=r.id WHERE r.completed_at IS NOT NULL AND r.engine_hash=%s
          ORDER BY r.created_at DESC LIMIT 50""",
            (engine_hash(),),
        ).fetchall()
    return money_strings(serial(rows))


class RunRequest(BaseModel):
    period_start: datetime
    period_end: datetime
    knowledge_cutoff: datetime
    bank_grace_business_days: int = Field(default=2, ge=0, le=5)


@app.post("/api/runs")
def new_run(body: RunRequest, user=Depends(operator)):
    policy = {**POLICY, "bank_grace_business_days": body.bank_grace_business_days}
    return protected(
        run_close,
        body.period_start,
        body.period_end,
        body.knowledge_cutoff,
        user["actor"],
        policy,
    )


@app.post("/api/packets/{name}")
def packet(
    name: Literal["initial", "repair_data", "repair_bank"], user=Depends(operator)
):
    manifest = json.loads((ROOT / "fixtures/manifest.json").read_text())
    item = next(x for x in manifest["packets"] if x["name"] == name)
    return protected(ingest_packet, item)


@app.get("/api/runs/{run_id}")
def detail(run_id: UUID, currency: Literal["SGD", "USD", "JPY"] = "SGD"):
    with connection() as c:
        run = c.execute("SELECT * FROM close_run WHERE id=%s", (run_id,)).fetchone()
        if not run:
            raise HTTPException(404, "Run not found")
        rows = c.execute(
            "SELECT * FROM control_result WHERE run_id=%s AND currency=%s ORDER BY kind,entity_key",
            (run_id, currency),
        ).fetchall()
        counts = c.execute(
            "SELECT kind,status,severity,count(*) n FROM control_result WHERE run_id=%s GROUP BY kind,status,severity ORDER BY kind,status",
            (run_id,),
        ).fetchall()
        cert = c.execute(
            "SELECT * FROM certification WHERE run_id=%s", (run_id,)
        ).fetchone()
        latest = c.execute(
            "SELECT max(observed_at) value FROM import_batch"
        ).fetchone()["value"]
        charge_records = c.execute(
            """SELECT coalesce(sum((detail->>'ledger_count')::integer),0) n FROM control_result
          WHERE run_id=%s AND kind='transaction' AND detail->>'category'='charge' AND detail->>'ledger_currency'=%s""",
            (run_id, currency),
        ).fetchone()["n"]
    get = lambda kind: [x["detail"] for x in rows if x["kind"] == kind]
    return money_strings(
        {
            "run": serial(run),
            "currency": currency,
            "counts": counts,
            "balance": get("balance"),
            "costs": get("cost"),
            "cost_coverage": {
                "internal_charge_records": charge_records,
                "comparable_charge_records": sum(x["charges"] for x in get("cost")),
            },
            "payouts": get("payout"),
            "aging": get("aging"),
            "certification": serial(cert),
            "newer_evidence_available": bool(
                latest and latest > run["knowledge_cutoff"]
            ),
        }
    )


KINDS = Literal[
    "all",
    "transaction",
    "membership",
    "payout",
    "balance",
    "quality",
    "bank",
    "coverage",
    "cost",
    "aging",
]
SEVERITY = Literal["all", "blocker", "advisory", "pending", "pass"]


@app.get("/api/runs/{run_id}/queue")
def queue(
    run_id: UUID,
    currency: Literal["all", "SGD", "USD", "JPY"] = "all",
    kind: KINDS = "all",
    severity: SEVERITY = "blocker",
    page: int = Query(1, ge=1),
    limit: int = Query(12, ge=1, le=100),
):
    where = " WHERE run_id=%s AND (%s='all' OR currency=%s) AND (%s='all' OR kind=%s) AND (%s='all' OR severity=%s)"
    args = [run_id, currency, currency, kind, kind, severity, severity]
    with connection() as c:
        total = c.execute(
            "SELECT count(*) n FROM control_result" + where, args
        ).fetchone()["n"]
        rows = c.execute(
            "SELECT * FROM control_result"
            + where
            + " ORDER BY kind,entity_key LIMIT %s OFFSET %s",
            args + [limit, (page - 1) * limit],
        ).fetchall()
    return money_strings({"total": total, "page": page, "limit": limit, "rows": rows})


@app.get("/api/runs/{run_id}/evidence")
def evidence(
    run_id: UUID, kind: KINDS, entity_key: str = Query(min_length=1, max_length=240)
):
    with connection() as c:
        row = c.execute(
            "SELECT * FROM control_result WHERE run_id=%s AND kind=%s AND entity_key=%s",
            (run_id, kind, entity_key),
        ).fetchone()
        if not row:
            raise HTTPException(404, "Result not found")
        ids = set()

        def collect(v):
            if isinstance(v, dict):
                for k, x in v.items():
                    if k.endswith("record_id") and x:
                        ids.add(x)
                    elif k.endswith("record_ids") and x:
                        ids.update(x)
            elif isinstance(v, list):
                for x in v:
                    collect(x)

        collect(row["detail"])
        records = (
            c.execute(
                """SELECT o.*,b.filename,b.sha256 file_sha256,b.ingested_at FROM observation o
          JOIN import_batch b ON o.batch_id=b.id WHERE o.id=ANY(%s) ORDER BY source,business_key""",
                ([UUID(x) for x in ids],),
            ).fetchall()
            if ids
            else []
        )
        notes = c.execute(
            "SELECT actor,note,created_at FROM case_note WHERE run_id=%s AND kind=%s AND entity_key=%s ORDER BY created_at",
            (run_id, kind, entity_key),
        ).fetchall()
    return money_strings({"result": row, "source_records": records, "notes": notes})


class NoteRequest(BaseModel):
    kind: KINDS
    entity_key: str = Field(min_length=1, max_length=240)
    note: str = Field(min_length=12, max_length=2000)


@app.post("/api/runs/{run_id}/notes")
def note(run_id: UUID, body: NoteRequest, user=Depends(operator)):
    if len(body.note.strip()) < 12:
        raise HTTPException(422, "Add an evidence-based note")
    with connection() as c:
        if not c.execute(
            "SELECT 1 FROM control_result WHERE run_id=%s AND kind=%s AND entity_key=%s",
            (run_id, body.kind, body.entity_key),
        ).fetchone():
            raise HTTPException(404, "Result not found")
        c.execute(
            "INSERT INTO case_note(id,run_id,kind,entity_key,actor,note) VALUES(%s,%s,%s,%s,%s,%s)",
            (
                uuid4(),
                run_id,
                body.kind,
                body.entity_key,
                user["actor"],
                body.note.strip(),
            ),
        )
    return {"saved": True, "financial_results_changed": False}


class SignRequest(BaseModel):
    note: str = Field(min_length=12, max_length=2000)


@app.post("/api/runs/{run_id}/certify")
def sign(run_id: UUID, body: SignRequest, user=Depends(operator)):
    return protected(certify, run_id, user["actor"], user["role"], body.note)


@app.get("/api/runs/{run_id}/export.csv")
def export(
    run_id: UUID,
    currency: Literal["all", "SGD", "USD", "JPY"] = "all",
    severity: SEVERITY = "blocker",
    kind: KINDS = "all",
):
    with connection() as c:
        rows = c.execute(
            """SELECT kind,entity_key,currency,status,severity,detail FROM control_result
         WHERE run_id=%s AND (%s='all' OR currency=%s) AND (%s='all' OR severity=%s)
         AND (%s='all' OR kind=%s) ORDER BY kind,entity_key""",
            (run_id, currency, currency, severity, severity, kind, kind),
        ).fetchall()
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["kind", "entity_key", "currency", "status", "severity", "detail"])

    def safe(v):
        return (
            "'" + v
            if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r"))
            else v
        )

    for row in rows:
        writer.writerow(
            [
                safe(
                    json.dumps(serial(v), ensure_ascii=False)
                    if isinstance(v, dict)
                    else v
                )
                for v in row.values()
            ]
        )
    return Response(
        out.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="close_controls.csv"'},
    )


@app.get("/api/runs/{run_id}/memo.html", include_in_schema=False)
def memo(run_id: UUID):
    from .reporting import close_memo

    return Response(protected(close_memo, str(run_id)), media_type="text/html")


@app.get("/guide", include_in_schema=False)
def guide():
    return FileResponse(ROOT / "docs/briefing.html")
