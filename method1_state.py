"""Persistent Method 1 Excel, input choices and calculated allocation.

Stored separately from active Method 2 order queue. Saving plans does not issue
any Ozon API calls. Streamlit instance-local disk survives normal page reloads
but is not guaranteed to survive server redeploys/restarts.
"""
import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("OZON_METHOD1_DB_PATH", "/tmp/ozon_method1_state.sqlite3"))
_LOCK = threading.RLock()


@contextmanager
def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA journal_mode=WAL")
        yield db
    finally:
        db.close()


def setup():
    with _LOCK, connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS report (
            id INTEGER PRIMARY KEY CHECK(id=1), filename TEXT NOT NULL,
            content BLOB NOT NULL, sha256 TEXT NOT NULL, saved_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS setting(
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS saved_plan(
            kind TEXT PRIMARY KEY, signature TEXT NOT NULL, sku TEXT,
            rows TEXT NOT NULL, saved_at TEXT NOT NULL
        )""")


def save_report(filename, content):
    setup()
    name = str(filename)
    data = bytes(content)
    digest = hashlib.sha256(data).hexdigest()
    with _LOCK, connect() as db:
        old = db.execute("SELECT filename, sha256 FROM report WHERE id=1").fetchone()
        if old is not None and old["sha256"] == digest and old["filename"] == name:
            return digest, False
        db.execute(
            "INSERT INTO report(id,filename,content,sha256,saved_at) VALUES(1,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET filename=excluded.filename,"
            "content=excluded.content,sha256=excluded.sha256,saved_at=excluded.saved_at",
            (name, sqlite3.Binary(data), digest, datetime.now(timezone.utc).isoformat()),
        )
        # A plan is tied to both the report content and its format/name.
        db.execute("DELETE FROM saved_plan WHERE kind='excel'")
        return digest, True


def load_report():
    setup()
    with _LOCK, connect() as db:
        row = db.execute("SELECT filename,content,sha256 FROM report WHERE id=1").fetchone()
        return {
            "name": row["filename"], "content": bytes(row["content"]),
            "sha256": row["sha256"],
        } if row else None


def get_setting(key, default=None):
    setup()
    with _LOCK, connect() as db:
        row = db.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default


def set_setting(key, value):
    setup()
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    with _LOCK, connect() as db:
        existing = db.execute("SELECT value FROM setting WHERE key=?", (key,)).fetchone()
        if existing is None or existing["value"] != serialized:
            db.execute(
                "INSERT INTO setting(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(key), serialized),
            )


def save_plan(kind, signature, sku, rows):
    setup()
    with _LOCK, connect() as db:
        db.execute(
            "INSERT INTO saved_plan(kind,signature,sku,rows,saved_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(kind) DO UPDATE SET signature=excluded.signature,sku=excluded.sku,"
            "rows=excluded.rows,saved_at=excluded.saved_at",
            (
                str(kind), str(signature), str(sku),
                json.dumps(rows, ensure_ascii=False),
                datetime.now(timezone.utc).isoformat(),
            ),
        )


def get_plan(kind, expected_signature):
    setup()
    with _LOCK, connect() as db:
        row = db.execute(
            "SELECT signature,sku,rows FROM saved_plan WHERE kind=?", (str(kind),)
        ).fetchone()
        if row is None or row["signature"] != expected_signature:
            return None
        return {"sku": row["sku"], "rows": json.loads(row["rows"])}


def signature_for(data):
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
