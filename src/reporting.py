"""Generate a finance-facing memo from persisted controls, never from expected fixture answers."""

from collections import Counter
from decimal import Decimal
from html import escape
from .db import connection


def amount(value, currency):
    if value is None:
        return "—"
    return format(
        Decimal(str(value)) / (1 if currency == "JPY" else 100),
        ",.0f" if currency == "JPY" else ",.2f",
    )


def close_memo(run_id):
    with connection() as c:
        run = c.execute("SELECT * FROM close_run WHERE id=%s", (run_id,)).fetchone()
        if not run:
            raise LookupError("Run not found")
        rows = c.execute(
            "SELECT * FROM control_result WHERE run_id=%s ORDER BY kind,entity_key",
            (run_id,),
        ).fetchall()
        latest = c.execute(
            "SELECT max(observed_at) value FROM import_batch"
        ).fetchone()["value"]
    balances = [x["detail"] for x in rows if x["kind"] == "balance"]
    costs = [x["detail"] for x in rows if x["kind"] == "cost"]
    blockers = Counter(
        (x["kind"], x["status"]) for x in rows if x["severity"] == "blocker"
    )
    pending = [
        x["detail"]
        for x in rows
        if x["kind"] == "payout" and x["status"] == "bank_pending"
    ]
    esc = lambda v: escape(str(v))
    stats = run["stats"]
    n = stats["severity_counts"].get("blocker", 0)
    btable = "".join(
        "<tr><th>"
        + x["currency"]
        + "</th>"
        + "".join(
            "<td>" + amount(x.get(k), x["currency"]) + "</td>"
            for k in [
                "opening_minor",
                "activity_minor",
                "movement_minor",
                "calculated_ending_minor",
                "ending_minor",
                "residual_minor",
            ]
        )
        + "</tr>"
        for x in balances
    )
    ct = ""
    for x in costs:
        internal = sum(
            (z["detail"].get("ledger_count") or 0)
            for z in rows
            if z["kind"] == "transaction"
            and z["detail"].get("category") == "charge"
            and z["detail"].get("ledger_currency") == x["currency"]
        )
        comparable = sum(z["charges"] for z in costs if z["currency"] == x["currency"])
        ct += f"<tr><td>{x['currency']}</td><td>{esc(x['partner'])}</td><td>{x['charges']}</td><td>{amount(x['charge_gross_minor'], x['currency'])}</td><td>{esc(x['observed_fee_bps'])}</td><td>{amount(x['variance_minor'], x['currency'])}</td><td>{comparable}/{internal}</td></tr>"
    actions = {
        "transaction": "Resolve business keys, currency and gross/fee components against original source evidence.",
        "membership": "Correct transaction membership before trusting the batch sum.",
        "payout": "Inspect payout amount, signed movements, bank reference and in-period booking.",
        "bank": "Obtain a verified payout reference; do not allocate using amount alone.",
        "balance": "Reconcile source coverage and summary identity; never plug a residual.",
        "quality": "Obtain a corrected source row; retain the invalid version in the audit trail.",
        "coverage": "Obtain the missing contracted source before close.",
    }
    bt = (
        "".join(
            f"<tr><td>{esc(kind)}</td><td>{esc(status.replace('_', ' '))}</td><td>{count}</td><td>{actions.get(kind, 'Inspect source evidence.')}</td></tr>"
            for (kind, status), count in sorted(blockers.items())
        )
        or '<tr><td colspan="4">No blocking controls remain in this evidence state.</td></tr>'
    )
    pt = "".join(
        f"<li><code>{esc(x['entity_key'])}</code> — {x['currency']} {amount(x['payout_amount_minor'], x['currency'])}; estimated arrival {esc(x['arrival_date'])}; investigation deadline {esc(x['bank_deadline'])}.</li>"
        for x in pending
    )
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><title>Clearset · Finance close memo</title><style>
    *{{box-sizing:border-box}}body{{font:14px/1.65 'Segoe UI',sans-serif;color:#243b33;max-width:1080px;margin:45px auto;padding:0 28px;background:#fafbf7}}h1{{font:38px Georgia;margin:8px 0}}h2{{font-size:19px;margin:30px 0 9px}}.eyebrow{{font-size:10px;letter-spacing:2px;color:#66816b}}.lede{{font-size:17px;color:#45664d;border-left:3px solid #709777;padding:13px 20px;background:#eef4e9}}.meta,small{{color:#84917f;font-size:11px}}table{{width:100%;border-collapse:collapse;font-size:11px}}th,td{{text-align:left;padding:9px 7px;border-bottom:1px solid #dce5d5}}th{{background:#edf3e8}}code{{font-size:11px;overflow-wrap:anywhere}}a{{color:#2f7154}}.callout{{background:#eef3e9;padding:16px 20px}}@media print{{body{{margin:0;background:white;font-size:10px;max-width:none}}h1{{font-size:27px}}h2{{font-size:15px;margin-top:19px}}table{{font-size:8px}}th,td{{padding:5px}}.lede{{font-size:12px}}section{{break-inside:avoid}}a{{color:inherit}}}}
    </style><div class="eyebrow">CLEARSET CLOSE / FINANCIAL OPERATIONS</div><h1>Finance close memo</h1><p class="meta">Business period: {esc(run["period_start"])} ≤ effective time &lt; {esc(run["period_end"])}<br>Knowledge cutoff: {esc(run["knowledge_cutoff"])} · Snapshot {esc(run_id)}<br>Fingerprint: <code>{esc(run["fingerprint"])}</code></p>
    <p class="lede">{"Hold close: " + str(n) + " blocking control findings require source correction." if n else "Ready for independent review: no blocking control findings remain."} Ledger-key match rate: {stats["ledger_key_match_rate"]:.2f}% ({stats["matched_ledger_keys"]}/{stats["ledger_keys"]}).</p>
    <p>{"This is a historical evidence state; newer source evidence is available." if latest and latest > run["knowledge_cutoff"] else "This snapshot includes the latest supplied source evidence."} Findings can be correlated across control layers; counts must not be interpreted as unique affected transactions or summed cash exposure.</p>
    <section><h2>1 / Balance bridge, by settlement currency</h2><table><tr><th>Currency</th><th>Opening</th><th>Net activity</th><th>Signed payouts</th><th>Calculated close</th><th>Reported close</th><th>Residual</th></tr>{btable}</table><p class="meta">Major currency units shown. Opening + net activity + signed payout movements = calculated close. Bank credits are separate evidence and never added to this bridge. No FX conversion or cross-currency total.</p></section>
    <section><h2>2 / Investigation priorities</h2><table><tr><th>Family</th><th>Finding</th><th>Count</th><th>Required next action</th></tr>{bt}</table><p>Correct the underlying extract through a new observed revision or explicit retraction, then rerun the close. Notes document an investigation and cannot make an unmatched amount pass.</p></section>
    <section><h2>3 / Timing and retained balance review</h2><ul>{pt or "<li>No pending payout timing items.</li>"}</ul><p>The demo uses a {run["policy"]["bank_grace_business_days"]}-business-day investigation grace after estimated arrival. Only weekends and explicit supplied holiday dates are excluded. This is a proposed policy, not a bank SLA or a verified Singapore holiday calendar. Report publication time, estimated payout arrival and bank booking are distinct clocks.</p><p>Unallocated matured activity remains an advisory. Funds can be retained or covered by manual payout behavior; absence of automatic membership does not establish unpaid or lost cash.</p></section>
    <section><h2>4 / Charge fee analysis</h2><table><tr><th>Currency</th><th>Partner</th><th>Charges</th><th>Charge gross</th><th>Fee bps</th><th>Fee variance</th><th>Currency coverage</th></tr>{ct}</table><p class="meta">Fee bps = Σ observed fees / Σ charge gross × 10,000. Variance = observed minus expected fees. Coverage repeats the comparable / internal charge-record count for that currency. Refunds, disputes, duplicate keys, missing rows and invalid arithmetic are excluded from this comparison. Partner schedules are fictional and are not Stripe pricing.</p></section>
    <div class="callout"><strong>Reviewer decision</strong><p>Confirm source completeness, zero blocking findings, pending bank deadlines and retained balance explanations. Certification is a separate append-only demo record tied to the source fingerprint. The maker cannot sign their own run.</p></div>
    <h2>Scope and provenance</h2><p>Independent offline portfolio project, not affiliated with Stripe. Synthetic economic events, intentionally flawed source extracts and explicit repair packets. Public Stripe documentation informs selected field contracts; these normalized subsets are not a drop-in production Report API integration. No actual customers, funds, external API calls, measured business savings or production assurance are represented.</p><p class="meta">Engine {esc(run["engine_hash"])}. {len(run["lineage"])} source batches recorded with SHA-256, observation time and ingestion time. Full evidence: <a href="/api/runs/{esc(run_id)}/export.csv?severity=all">control CSV</a> · <a href="/guide">methods and contracts</a>.</p></html>"""
