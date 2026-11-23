from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4
import hashlib, json, time
from psycopg.types.json import Jsonb
from .db import connection, lock, ROOT
from .contracts import decode, timestamp, payload_hash, ContractError, SOURCES

POLICY = {
    "version": "demo-control-policy-1",
    "bank_grace_business_days": 2,
    "holiday_dates": [],
    "calendar": "Weekends plus explicitly supplied holidays; not a verified bank calendar",
    "scope": "SGD/USD/JPY; automatic batch reconciliation; no FX conversion; no payout fees",
}


class Conflict(ValueError):
    pass


class Permission(ValueError):
    pass


def serial(value):
    if isinstance(value, dict):
        return {k: serial(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serial(v) for v in value]
    if isinstance(value, (datetime, date, Decimal)):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    return value


def ingest_packet(packet, fixture_root=None):
    """Validate all files first, then commit the whole source packet atomically."""
    prepared = []
    fixture_root = Path(fixture_root or ROOT / "fixtures").resolve()
    for item in packet["files"]:
        path = (fixture_root / item["file"]).resolve()
        if not path.is_relative_to(fixture_root):
            raise ContractError("Invalid fixture path")
        body = path.read_bytes()
        observed = timestamp(item.get("observed_at", packet["observed_at"]))
        revision = item.get("revision", 1)
        retracted = item.get("retracted", False)
        if type(revision) is not int or revision < 1:
            raise ContractError("Revision must be a positive integer")
        if type(retracted) is not bool:
            raise ContractError("Retraction flag must be a boolean")
        records = decode(item["source"], body, packet["period_end"])
        envelope = json.dumps(
            {
                "observed_at": str(observed),
                "revision": revision,
                "retracted": retracted,
            },
            sort_keys=True,
        ).encode()
        digest = hashlib.sha256(body + envelope).hexdigest()
        prepared.append((item, records, observed, revision, retracted, digest))
    results = []
    with connection() as c:
        lock(c)
        for item, records, observed, revision, retracted, digest in prepared:
            existing = c.execute(
                "SELECT id,record_count FROM import_batch WHERE source=%s AND sha256=%s",
                (item["source"], digest),
            ).fetchone()
            if existing:
                results.append(
                    {
                        "source": item["source"],
                        "replayed": True,
                        "rows": existing["record_count"],
                        "batch_id": str(existing["id"]),
                    }
                )
                continue
            batch_id = uuid4()
            inserts = []
            for business, effective, payload in records:
                digest_payload = payload_hash(payload, retracted)
                versions = c.execute(
                    "SELECT revision,observed_at,payload_sha256 FROM observation WHERE source=%s AND business_key=%s ORDER BY revision",
                    (item["source"], business),
                ).fetchall()
                same = next((r for r in versions if r["revision"] == revision), None)
                if same:
                    if same["payload_sha256"] != digest_payload:
                        raise ContractError(
                            "Conflicting values for an existing source revision"
                        )
                    continue
                if any(
                    (r["revision"] < revision and r["observed_at"] > observed)
                    or (r["revision"] > revision and r["observed_at"] < observed)
                    for r in versions
                ):
                    raise ContractError("Revision order contradicts observation time")
                if retracted and not versions:
                    raise ContractError("Cannot retract an unknown source record")
                inserts.append(
                    (
                        uuid4(),
                        item["source"],
                        business,
                        revision,
                        observed,
                        effective,
                        retracted,
                        Jsonb(payload),
                        digest_payload,
                        batch_id,
                    )
                )
            c.execute(
                "INSERT INTO import_batch(id,source,sha256,filename,observed_at,record_count) VALUES(%s,%s,%s,%s,%s,%s)",
                (
                    batch_id,
                    item["source"],
                    digest,
                    item["file"],
                    observed,
                    len(records),
                ),
            )
            if inserts:
                with c.cursor().copy(
                    "COPY observation(id,source,business_key,revision,observed_at,effective_at,retracted,payload,payload_sha256,batch_id) FROM STDIN"
                ) as cp:
                    for row in inserts:
                        cp.write_row(row)
            results.append(
                {
                    "source": item["source"],
                    "replayed": False,
                    "rows": len(records),
                    "new_versions": len(inserts),
                    "batch_id": str(batch_id),
                }
            )
    return results


def engine_hash():
    digest = hashlib.sha256()
    for p in sorted(
        list((ROOT / "sql").glob("0[123456]*.sql"))
        + [ROOT / "src/engine.py", ROOT / "src/contracts.py"]
    ):
        digest.update(p.name.encode())
        digest.update(p.read_bytes())
    return digest.hexdigest()


def selected_rows(c, cutoff):
    return c.execute(
        """SELECT source,business_key,revision,retracted,id,payload_sha256,batch_id FROM
      (SELECT *,row_number() OVER(PARTITION BY source,business_key ORDER BY revision DESC) n
       FROM observation WHERE observed_at<=%s) r WHERE n=1 ORDER BY source,business_key""",
        (cutoff,),
    ).fetchall()


def fingerprint(c, start, end, cutoff, policy):
    rows = selected_rows(c, cutoff)
    eh = engine_hash()
    # PostgreSQL may return the same instant in its session time zone. Canonicalize before hashing.
    data = {
        "period_start": start.astimezone(timezone.utc).isoformat(),
        "period_end": end.astimezone(timezone.utc).isoformat(),
        "cutoff": cutoff.astimezone(timezone.utc).isoformat(),
        "policy": policy,
        "engine_hash": eh,
        "records": serial(rows),
    }
    return (
        hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        rows,
        eh,
    )


def prepare_views(c, params):
    script = (ROOT / "sql/010_snapshot_views.sql").read_text()
    for statement in script.split(";"):
        if statement.strip():
            c.execute(statement, params if "%(" in statement else None)


def query(c, name, params):
    return c.execute((ROOT / "sql" / name).read_text(), params).fetchall()


def business_deadline(arrival, days, holidays):
    current = arrival
    left = days
    excluded = {date.fromisoformat(x) for x in holidays}
    while left:
        current += timedelta(days=1)
        if current.weekday() < 5 and current not in excluded:
            left -= 1
    return current


def run_close(start, end, cutoff, actor="demo-analyst", policy=None):
    start = timestamp(start)
    end = timestamp(end)
    cutoff = timestamp(cutoff)
    policy = dict(policy or POLICY)
    if not start < end or cutoff < end:
        raise ValueError("Use start < end <= knowledge cutoff")
    if policy["bank_grace_business_days"] not in range(0, 6):
        raise ValueError("Grace period must be 0 to 5 business days")
    for x in policy["holiday_dates"]:
        date.fromisoformat(x)
    began = time.perf_counter()
    params = {"start": start, "end": end, "cutoff": cutoff}
    output = []
    as_of = (
        end.astimezone(timezone(timedelta(hours=8))) - timedelta(microseconds=1)
    ).date()

    def add(kind, row, severity=None):
        row = serial(row)
        status = row["status"]
        if severity is None:
            severity = "pass" if status == "matched" else "blocker"
        output.append(
            {
                "kind": kind,
                "entity_key": row["entity_key"],
                "currency": row.get("currency"),
                "status": status,
                "severity": severity,
                "detail": row,
            }
        )

    with connection() as c:
        lock(c)
        fp, selected, eh = fingerprint(c, start, end, cutoff, policy)
        existing = c.execute(
            "SELECT * FROM close_run WHERE fingerprint=%s", (fp,)
        ).fetchone()
        if existing:
            return {**serial(existing), "replayed": True}
        prepare_views(c, params)
        rid = uuid4()
        active_counts = Counter(r["source"] for r in selected if not r["retracted"])
        for source in sorted(SOURCES):
            add(
                "coverage",
                {
                    "entity_key": source,
                    "status": "matched" if active_counts[source] else "missing_source",
                    "active_records": active_counts[source],
                },
            )
        txns = query(c, "020_transactions.sql", params)
        for row in txns:
            add("transaction", row)
        members = query(c, "030_membership.sql", params)
        bad_members = Counter()
        for row in members:
            add("membership", row)
            if row["status"] != "matched":
                bad_members[row["payout_id"]] += 1
        for row in query(c, "040_payouts.sql", params):
            failed = row["payout_status"] in ["failed", "canceled"]
            arrived = row["arrival_date"] <= as_of
            if failed or not row["automatic"]:
                batch = "not_applicable"
            elif not row["membership_count"]:
                batch = "missing_membership"
            elif bad_members[row["entity_key"]]:
                batch = "untrusted_membership"
            elif (
                row["membership_currencies"] != 1
                or row["membership_currency"] != row["currency"]
            ):
                batch = "batch_currency_mismatch"
            elif row["batch_delta_minor"] != 0:
                batch = "batch_amount_mismatch"
            else:
                batch = "matched"
            expected_movement = (
                0 if failed or not arrived else -row["payout_amount_minor"]
            )
            if row["movement_count"] and (
                not row["movement_arithmetic_valid"]
                or row["movement_currencies"] != 1
                or row["movement_currency"] != row["currency"]
            ):
                movement = "invalid_movement_evidence"
            elif row["movement_fee_minor"]:
                movement = "unsupported_payout_fee"
            elif (row["movement_gross_minor"] or 0) != expected_movement:
                movement = "payout_movement_mismatch"
            else:
                movement = "matched"
            deadline = business_deadline(
                row["arrival_date"],
                policy["bank_grace_business_days"],
                policy["holiday_dates"],
            )
            if failed:
                bank = (
                    "unexpected_failed_payout_credit"
                    if row["bank_count"]
                    else "not_expected"
                )
            elif row["bank_count"] > 1:
                bank = "ambiguous_bank_reference"
            elif row["bank_count"] == 1 and row["bank_currency"] != row["currency"]:
                bank = "bank_currency_mismatch"
            elif row["bank_count"] == 1 and row["bank_delta_minor"] != 0:
                bank = "bank_amount_mismatch"
            elif row["bank_count"] == 1:
                bank = "matched"
            else:
                bank = "bank_missing" if deadline <= as_of else "bank_pending"
            blockers = [
                x
                for x in [batch, movement, bank]
                if x
                not in ["matched", "not_applicable", "not_expected", "bank_pending"]
            ]
            status = (
                blockers[0]
                if blockers
                else ("bank_pending" if bank == "bank_pending" else "matched")
            )
            row.update(
                status=status,
                batch_status=batch,
                movement_status=movement,
                bank_status=bank,
                expected_movement_minor=expected_movement,
                bank_deadline=str(deadline),
                as_of=str(as_of),
            )
            add(
                "payout",
                row,
                "blocker"
                if blockers
                else "pending"
                if bank == "bank_pending"
                else "pass",
            )
        for row in query(c, "050_balance_bridge.sql", params):
            if row["summary_categories"] != 4:
                status = "missing_summary_categories"
            elif row["summary_identity_delta_minor"] != 0:
                status = "summary_identity_mismatch"
            elif row["activity_delta_minor"] != 0:
                status = "activity_total_mismatch"
            elif row["payout_delta_minor"] != 0:
                status = "payout_total_mismatch"
            elif row["residual_minor"] != 0:
                status = "balance_residual"
            else:
                status = "matched"
            row["status"] = status
            add("balance", row)
        for row in query(c, "060_costs_and_aging.sql", params):
            row.update(
                entity_key=row["currency"] + ":" + row["partner"],
                status="fee_variance" if row["variance_minor"] else "matched",
            )
            add("cost", row, "advisory" if row["variance_minor"] else "pass")
        # Retained balance: separate maturity from overdue investigation. It is not lost cash.
        aging = c.execute(
            """SELECT a.*,floor(extract(epoch FROM (%s-a.available_on))/86400)::integer age_days
          FROM activity_all a WHERE a.effective_at<%s AND NOT EXISTS
          (SELECT 1 FROM member_all m WHERE m.transaction_id=a.business_key AND m.effective_at<%s)""",
            (end, end, end),
        ).fetchall()
        for row in aging:
            days = row.pop("age_days")
            status = (
                "not_yet_available"
                if days < 0
                else "unallocated_aged"
                if days >= 3
                else "available_unallocated"
            )
            row.update(
                entity_key=row.pop("business_key"),
                activity_record_id=row.pop("id"),
                age_days=max(days, 0),
                status=status,
                net_minor=row.pop("net"),
            )
            add("aging", row, "advisory" if status == "unallocated_aged" else "pending")
        for row in c.execute(
            """SELECT b.business_key entity_key,b.currency,b.amount amount_minor,b.payout_reference,b.id bank_record_id
          FROM bank_all b WHERE b.effective_at<%s AND NOT EXISTS(SELECT 1 FROM payout_all p WHERE p.business_key=b.payout_reference)""",
            (end,),
        ):
            candidates = c.execute(
                """SELECT business_key FROM payout_all WHERE currency=%s AND amount=%s
              AND effective_at<%s ORDER BY business_key""",
                (row["currency"], row["amount_minor"], end),
            ).fetchall()
            row.update(
                status="unmapped_bank_credit",
                candidate_payouts=[p["business_key"] for p in candidates],
                candidate_rule="Same currency/amount only; suggestions never auto-match",
            )
            add("bank", row)
        # Explicit row-level arithmetic checks remain visible even when grouped controls also fail.
        for source, table in [
            ("activity", "activity_all"),
            ("membership", "member_all"),
            ("movements", "movement_all"),
        ]:
            for row in c.execute(
                f"SELECT id,business_key entity_key,currency,gross gross_minor,fee fee_minor,net net_minor FROM {table} WHERE gross-fee<>net AND effective_at<%s",
                (end,),
            ):
                row.update(
                    entity_key=source + ":" + row["entity_key"],
                    source_record_id=row.pop("id"),
                    status="source_arithmetic_error",
                )
                add("quality", row)
        counts = Counter(x["severity"] for x in output)
        by_kind = {}
        for kind in sorted({x["kind"] for x in output}):
            by_kind[kind] = dict(
                Counter(x["status"] for x in output if x["kind"] == kind)
            )
        ledger_keys = [x for x in txns if x["ledger_count"]]
        stats = {
            "results": len(output),
            "severity_counts": dict(counts),
            "by_kind": by_kind,
            "ledger_keys": len(ledger_keys),
            "matched_ledger_keys": sum(x["status"] == "matched" for x in ledger_keys),
            "ledger_key_match_rate": round(
                100
                * sum(x["status"] == "matched" for x in ledger_keys)
                / len(ledger_keys),
                4,
            )
            if ledger_keys
            else 0,
            "ready_for_review": counts["blocker"] == 0,
            "source_records": sum(active_counts.values()),
            "duration_ms_before_commit": round((time.perf_counter() - began) * 1000, 3),
        }
        batch_ids = list({r["batch_id"] for r in selected})
        lineage = (
            c.execute(
                "SELECT id,source,filename,sha256,observed_at,ingested_at,record_count FROM import_batch WHERE id=ANY(%s) ORDER BY source,observed_at",
                (batch_ids,),
            ).fetchall()
            if batch_ids
            else []
        )
        c.execute(
            """INSERT INTO close_run(id,period_start,period_end,knowledge_cutoff,created_by,fingerprint,policy,engine_hash,lineage)
          VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                rid,
                start,
                end,
                cutoff,
                actor,
                fp,
                Jsonb(policy),
                eh,
                Jsonb(serial(lineage)),
            ),
        )
        with c.cursor().copy(
            "COPY control_result(run_id,kind,entity_key,currency,status,severity,detail) FROM STDIN"
        ) as cp:
            for row in output:
                cp.write_row(
                    (
                        rid,
                        row["kind"],
                        row["entity_key"],
                        row["currency"],
                        row["status"],
                        row["severity"],
                        Jsonb(row["detail"]),
                    )
                )
        c.execute(
            "UPDATE close_run SET stats=%s,completed_at=clock_timestamp() WHERE id=%s",
            (Jsonb(stats), rid),
        )
    return {
        "id": str(rid),
        "period_start": str(start),
        "period_end": str(end),
        "knowledge_cutoff": str(cutoff),
        "fingerprint": fp,
        "stats": stats,
        "replayed": False,
    }


def certify(run_id, actor, role, note):
    if role != "reviewer":
        raise Permission("Only the reviewer can certify a close")
    if len(note.strip()) < 12:
        raise ValueError("A review note of at least 12 characters is required")
    with connection() as c:
        lock(c)
        run = c.execute(
            "SELECT * FROM close_run WHERE id=%s FOR UPDATE", (run_id,)
        ).fetchone()
        if not run:
            raise LookupError("Run not found")
        if actor == run["created_by"]:
            raise Permission("Maker and reviewer must be different identities")
        if not run["stats"]["ready_for_review"]:
            raise Conflict(
                "Blocking financial controls remain; a note cannot clear them"
            )
        newest = c.execute(
            "SELECT max(observed_at) latest FROM import_batch"
        ).fetchone()["latest"]
        if newest > run["knowledge_cutoff"]:
            raise Conflict(
                "Newer source evidence is available; restate before certification"
            )
        fp, _, _ = fingerprint(
            c,
            run["period_start"],
            run["period_end"],
            run["knowledge_cutoff"],
            run["policy"],
        )
        if fp != run["fingerprint"]:
            raise Conflict("Snapshot is stale against its source or engine version")
        if c.execute(
            "SELECT 1 FROM certification WHERE run_id=%s", (run_id,)
        ).fetchone():
            raise Conflict("Already certified")
        row = c.execute(
            "INSERT INTO certification(id,run_id,reviewer,note,fingerprint) VALUES(%s,%s,%s,%s,%s) RETURNING *",
            (uuid4(), run_id, actor, note.strip(), fp),
        ).fetchone()
        return serial(row)
