"""Independent persistent queue for Method 1 Ozon supplies."""
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("OZON_METHOD1_SUPPLY_DB_PATH", "/tmp/ozon_method1_supply.sqlite3"))
_LOCK = threading.RLock()


@contextmanager
def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        yield connection
    finally:
        connection.close()


def initialise():
    with _LOCK, _db() as db:
        db.execute("CREATE TABLE IF NOT EXISTS plan (id INTEGER PRIMARY KEY, signature TEXT UNIQUE, identity TEXT NOT NULL, window TEXT NOT NULL, created_at TEXT NOT NULL, stopped INTEGER NOT NULL DEFAULT 0)")
        db.execute("CREATE TABLE IF NOT EXISTS job (id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL, cluster_id INTEGER NOT NULL, body TEXT NOT NULL, UNIQUE(plan_id, cluster_id))")


def _read(db):
    row = db.execute("SELECT * FROM plan ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return None
    result = dict(row)
    result["identity"] = json.loads(result.pop("identity"))
    result["window"] = json.loads(result.pop("window"))
    result["jobs"] = []
    for item in db.execute("SELECT id,body FROM job WHERE plan_id=? ORDER BY id", (result["id"],)):
        body = json.loads(item["body"])
        body["_id"] = int(item["id"])
        result["jobs"].append(body)
    return result


def read_plan():
    initialise()
    with _LOCK, _db() as db:
        return _read(db)


def start_plan(signature, identity, rows, window):
    initialise()
    with _LOCK, _db() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            current = _read(db)
            if current:
                db.execute("COMMIT")
                return current, False
            cursor = db.execute("INSERT INTO plan(signature,identity,window,created_at) VALUES(?,?,?,?)", (
                signature, json.dumps(identity, ensure_ascii=False, sort_keys=True),
                json.dumps(window), datetime.now(timezone.utc).isoformat(),
            ))
            plan_id = cursor.lastrowid
            for row in rows:
                job = {
                    "name": str(row["Кластер"]), "cluster_id": int(row["ID Ozon"]),
                    "sku": int(identity["sku"]), "quantity": int(row["Отправить, шт."]),
                    "attempts": 0, "status": "WAITING", "reason": "", "order_id": None,
                }
                db.execute("INSERT INTO job(plan_id,cluster_id,body) VALUES(?,?,?)", (
                    plan_id, job["cluster_id"], json.dumps(job, ensure_ascii=False),
                ))
            db.execute("COMMIT")
            return _read(db), True
        except Exception:
            db.execute("ROLLBACK")
            raise


def save_job(job):
    initialise()
    data = {key: value for key, value in job.items() if key != "_id"}
    with _LOCK, _db() as db:
        db.execute("UPDATE job SET body=? WHERE id=?", (json.dumps(data, ensure_ascii=False), int(job["_id"])))


def stop_queue():
    initialise()
    with _LOCK, _db() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("UPDATE plan SET stopped=1")
            for row in db.execute("SELECT id,body FROM job"):
                job = json.loads(row["body"])
                if job.get("status") in ("WAITING", "RETRY"):
                    job["status"] = "STOPPED"
                    job["reason"] = "Остановлено пользователем"
                    db.execute("UPDATE job SET body=? WHERE id=?", (json.dumps(job, ensure_ascii=False), row["id"]))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise


def reserve_attempt(max_per_hour=45):
    initialise()
    now = time.time()
    with _LOCK, _db() as db:
        db.execute("CREATE TABLE IF NOT EXISTS rate_limit (id INTEGER PRIMARY KEY, stamp REAL NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM rate_limit WHERE stamp<?", (now - 3600,))
        count = db.execute("SELECT COUNT(*) AS n FROM rate_limit").fetchone()["n"]
        if count >= max_per_hour:
            db.execute("COMMIT")
            return False
        db.execute("INSERT INTO rate_limit(stamp) VALUES(?)", (now,))
        db.execute("COMMIT")
        return True
