# Clearset Close

An independent financial data controls lab for investigating payment settlement and closing discrepancies. Python validates seven source contracts; PostgreSQL reconciles ledger events, provider activity, automatic payout membership, platform movements and independently modeled bank credits. A finance workbench exposes exceptions and original evidence. No Stripe account or API key is required.

**Offline synthetic lab, developed with AI assistance. Not affiliated with Stripe. No production deployment, real funds, customers or business savings are represented.**

## Start locally on Windows

Prerequisites: Python 3.11+ with pip and native PostgreSQL 18 binaries. The Windows workflow was validated with Python 3.12.14. Install PostgreSQL separately; set `STRIPECLOSE_PG_BIN` to its `bin` directory if it is not at the default Windows path.

From a fresh clone, run `SETUP.ps1`, then `START.ps1` in PowerShell. No virtual environment or database is committed. The launcher initializes this project's isolated loopback PostgreSQL cluster, imports three synthetic evidence packets idempotently, and serves <http://127.0.0.1:8766>. Other operating-system launch workflows have not been validated.

```powershell
.\SETUP.ps1                 # first run after cloning
.\START.ps1                 # local app; Ctrl+C stops its web server
.\TEST.ps1                  # isolated stripeclose_test database only
.\.venv\Scripts\python.exe -m scripts.export_evidence
.\STOP_DATABASE.ps1         # stops only this portfolio's cluster; preserves data
```

The local app defaults to a demo analyst. Choose the separate demo reviewer to exercise certification. These visible local identities demonstrate maker/reviewer rules; they are not production authentication. The server binds to loopback only. Runtime database credentials remain in ignored `_runtime/state.json` and are excluded from source bundles.

## Five-minute demonstration

1. Initial close: inspect `ch_000000:charge`. Net matches, but gross and fee both differ. A net-only control would miss it.
2. Inspect `ch_000004:charge` and `po_USD_14`. Duplicate transaction keys and duplicate bank references are quarantined before joining or adding amounts.
3. Select source corrections: blocking findings fall from 31 to 6. Bank reference and settlement defects remain visible.
4. Select bank corrections: blocking findings fall to 0. Review the three currency balance bridges, the two pending payouts and retained balance advisory.
5. Replay the initial snapshot. Its source fingerprint and results remain unchanged after later corrections. Export the finance memo and inspect original source rows and batch hashes.
6. With the analyst selected, certification is forbidden. A separate reviewer can certify only a clean, current evidence state. Comments never clear a mismatch.

31/6/0 are correlated **control findings**, not distinct transactions or monetary impact. The final state still contains four pending control results and one advisory. A ready state means ready for review, not an automatically certified close.

## Evidence and structure

| Path | What a reviewer can inspect |
|---|---|
| `src/contracts.py` | Strict money conversion, currency exponents, timestamps and pinned source subsets |
| `src/engine.py` | Atomic import, version selection, fingerprints, control orchestration, review guards |
| `sql/020_transactions.sql` | Aggregate-before-join, independent gross/fee/net checks, duplicate quarantine |
| `sql/030_membership.sql` | Member-to-transaction checks and duplicate payout allocation detection |
| `sql/040_payouts.sql` | Batch, payout object, signed movement and bank-reference evidence |
| `sql/050_balance_bridge.sql` | Opening + activity + signed payout movement = ending, per currency |
| `sql/060_costs_and_aging.sql` | Charge-only weighted fee rates over the comparable subset |
| `scripts/generate_fixtures.py` | Independently generated economic truth and intentional report defects |
| `tests/` | Contract, integration, historical replay, permissions and financial edge cases |
| `artifacts/` | Saved three-stage memos, lineage and query measurements; regenerate full control CSVs with `scripts.export_evidence` |
| `docs/briefing.html` | Domain model, source references, design choices and limitations |

The test suite runs against real PostgreSQL, not an in-memory SQL substitute. Tests validate 13 named initial failures and independently calculated balances in SGD/USD/JPY, including source corrections, retractions, concurrent import, atomic rollback, zero-decimal money, failed payout return flows, same-amount bank ambiguity, timezone boundaries and maker/reviewer separation.

## Deliberate boundaries

The public Stripe documentation informs selected fields and accounting concepts; adapters accept exact normalized lab subsets, not complete live Report API exports. The internal ledger, partner fee schedules, bank extract and correction envelope are invented contracts. Automatic payout membership is used only where supplied; manual payouts are not allocated by amount. The payout summary is normalized to positive net outflow. Payout movement rows carry signed net and are counted once. Nonzero payout fees are explicitly blocked as unsupported.

SGD, USD and JPY are separate settlement currencies. No FX, exchange rate, tax, GAAP/IFRS statement, general ledger posting, customer/subledger completeness assurance or regulatory certification is implemented. Bank grace is a configurable investigation policy, not a bank SLA. Owner/superuser administration can alter the database; append-only triggers and application grants are not cryptographic tamper resistance. A production implementation would need authenticated ingestion, verified provider schemas, pagination, permissioned secrets, independent identity, secured hosting, migrations, backups and a verified business calendar.

## Reproducibility

`requirements-lock.txt` records the exact tested dependency versions. `fixtures/manifest.json` contains deterministic source hashes and independent expected results. The same file bytes and envelope replay idempotently. Changed source values require a new revision. Each snapshot records source batch lineage, code hash, policy, business interval and knowledge cutoff. The query plan and measured wall times in `artifacts/benchmark.json` cover a small local synthetic fixture; they do not establish production performance.

Generated finance memos are also accessible under `/api/runs/{run_id}/memo.html`; `/docs` exposes the API schema. The app makes no external API calls and submits no job applications. Source was prepared for public publication on 2026-11-23; all economic records are synthetic.

## Related work

[Payment Operations Lab](https://github.com/Lxkideis/payment-ops-lab) is the earlier two-source investigation workflow. [Data Operations Labs](https://github.com/Lxkideis/data-operations-labs) contains five foundational cases. These are independent synthetic applications.

## Generated inputs

Synthetic source CSVs and their manifest are generated deterministically on first startup by `scripts.seed_demo`. They are not committed. Saved evidence summaries remain under `artifacts/`; the generator code defines the injected defects and independent expected counts.
