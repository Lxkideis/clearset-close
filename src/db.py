from contextlib import contextmanager
from pathlib import Path
import json, os
import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def connection():
    url = os.getenv("STRIPECLOSE_DATABASE_URL")
    if not url:
        url = json.loads((ROOT / "_runtime/state.json").read_text())["app_url"]
    with psycopg.connect(url, row_factory=dict_row, connect_timeout=3) as c:
        c.execute("SET search_path TO closeops,public")
        c.execute("SET statement_timeout TO '25s'")
        yield c


def lock(c):
    c.execute("SELECT pg_advisory_xact_lock(hashtext('stripeclose_pipeline'))")
