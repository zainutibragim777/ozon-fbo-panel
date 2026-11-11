"""Server-side persistence for Method 2; survives browser refresh.

SQLite lives on Streamlit instance disk. Cloud redeploys/restarts can erase it;
do not mistake this for permanent off-instance storage. The API-create call is
write-ahead protected to prevent duplicates within this server lifetime.
"""
import json
import os
import sqlite3
from contextlib import contextmanager
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.environ.get("OZON_METHOD2_DB_PATH", "/tmp/ozon_method2_state.sqlite3"))
_LOCK = threading.RLock()
_WORKER = None
_RETRY_EVERY = 180
_API_LIMIT_SECONDS = 40
_MAX_PER_HOUR = 45


@contextmanager
def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(DB_PATH), timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        yield db
    finally:
        db.close()


def initialise():
    with _LOCK, _conn() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS preference(
            key TEXT PRIMARY KEY, value TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS document(
            sku TEXT PRIMARY KEY, file_blob BLOB NOT NULL,
            digest TEXT NOT NULL, quantity INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS plan(
            id INTEGER PRIMARY KEY,
            signature TEXT NOT NULL UNIQUE,
            identity_json TEXT NOT NULL,
            rows_json TEXT NOT NULL,
            window_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            stopped INTEGER NOT NULL DEFAULT 0
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS job(
            id INTEGER PRIMARY KEY,
            plan_id INTEGER NOT NULL REFERENCES plan(id),
            cluster_id INTEGER NOT NULL,
            body_json TEXT NOT NULL,
            UNIQUE(plan_id, cluster_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS rate_limit(
            id INTEGER PRIMARY KEY, stamp REAL NOT NULL
        )""")


def pref_get(key, fallback=None):
    initialise()
    with _LOCK, _conn() as db:
        row = db.execute("SELECT value FROM preference WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else fallback


def pref_set(key, value):
    initialise()
    with _LOCK, _conn() as db:
        db.execute(
            "INSERT INTO preference(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value, ensure_ascii=False)),
        )


def file_get(sku):
    initialise()
    with _LOCK, _conn() as db:
        row = db.execute(
            "SELECT file_blob,digest,quantity FROM document WHERE sku=?", (str(sku),)
        ).fetchone()
    return (bytes(row["file_blob"]), row["digest"], row["quantity"]) if row else None


def file_save(sku, data, digest, quantity):
    initialise()
    with _LOCK, _conn() as db:
        old = db.execute("SELECT digest,quantity FROM document WHERE sku=?", (str(sku),)).fetchone()
        if old and old["digest"] == digest and old["quantity"] == int(quantity):
            return
        db.execute(
            "INSERT INTO document(sku,file_blob,digest,quantity,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(sku) DO UPDATE SET "
            "file_blob=excluded.file_blob,digest=excluded.digest,"
            "quantity=excluded.quantity,updated_at=excluded.updated_at",
            (str(sku), sqlite3.Binary(data), digest, int(quantity), datetime.now(timezone.utc).isoformat()),
        )


def _read_plan(db):
    raw = db.execute("SELECT * FROM plan ORDER BY id DESC LIMIT 1").fetchone()
    if not raw:
        return None
    record = dict(raw)
    record["identity"] = json.loads(record.pop("identity_json"))
    record["rows"] = json.loads(record.pop("rows_json"))
    record["window"] = json.loads(record.pop("window_json"))
    rows = db.execute("SELECT id,body_json FROM job WHERE plan_id=? ORDER BY id", (record["id"],)).fetchall()
    record["jobs"] = [{**json.loads(r["body_json"]), "_id": int(r["id"])} for r in rows]
    return record


def read_plan():
    initialise()
    with _LOCK, _conn() as db:
        return _read_plan(db)


def start_plan(signature, identity, rows, window):
    """Atomic queue creation; never replace any existing plan."""
    initialise()
    with _LOCK, _conn() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            current = _read_plan(db)
            if current is not None:
                db.execute("COMMIT")
                return current, False
            cursor = db.execute(
                "INSERT INTO plan(signature,identity_json,rows_json,window_json,created_at) "
                "VALUES(?,?,?,?,?)",
                (
                    str(signature),
                    json.dumps(identity, ensure_ascii=False, sort_keys=True),
                    json.dumps(rows, ensure_ascii=False),
                    json.dumps(window),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            plan_id = cursor.lastrowid
            for row in rows:
                job = {
                    "name": str(row["Кластер"]),
                    "cluster_id": int(row["ID Ozon"]),
                    "sku": int(identity["sku"]),
                    "quantity": int(row["Отправить, шт."]),
                    "attempts": 0,
                    "status": "WAITING",
                    "reason": "",
                    "order_id": None,
                }
                # The user already created a Moscow order for this SKU in
                # the previous session; its order ID was not persisted.
                # Never automatically re-create that destination.
                if int(identity["sku"]) == 4357898852 and int(row["ID Ozon"]) == 4039:
                    job["status"] = "NEEDS_REVIEW"
                    job["reason"] = (
                        "Москва уже отправлялась до сброса старой сессии. "
                        "Проверяем существующую заявку Ozon; повторное создание запрещено."
                    )
                db.execute(
                    "INSERT INTO job(plan_id,cluster_id,body_json) VALUES(?,?,?)",
                    (plan_id, job["cluster_id"], json.dumps(job, ensure_ascii=False)),
                )
            db.execute("COMMIT")
            return _read_plan(db), True
        except Exception:
            db.execute("ROLLBACK")
            raise


def save_job(job):
    job_id = int(job["_id"])
    payload = {k: v for k, v in job.items() if k != "_id"}
    initialise()
    with _LOCK, _conn() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            row = db.execute("SELECT body_json FROM job WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise RuntimeError("Missing persisted job")
            old = json.loads(row["body_json"])
            if old.get("status") == "STOPPED" and payload.get("status") in ("WAITING", "RETRY"):
                payload["status"] = "STOPPED"
                payload["reason"] = "Пользователь остановил повторные попытки"
            db.execute(
                "UPDATE job SET body_json=? WHERE id=?",
                (json.dumps(payload, ensure_ascii=False), job_id),
            )
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise


def stop_queue():
    initialise()
    with _LOCK, _conn() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("UPDATE plan SET stopped=1")
            for row in db.execute("SELECT id,body_json FROM job").fetchall():
                job = json.loads(row["body_json"])
                if job.get("status") in ("WAITING", "RETRY"):
                    job["status"] = "STOPPED"
                    job["reason"] = "Остановлено пользователем"
                    db.execute("UPDATE job SET body_json=? WHERE id=?", (
                        json.dumps(job, ensure_ascii=False), row["id"],
                    ))
            db.execute("COMMIT")
        except Exception:
            db.execute("ROLLBACK")
            raise


def _reserve_attempt():
    """Share rate limiting across all worker ticks and reloads."""
    now = time.time()
    with _LOCK, _conn() as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("DELETE FROM rate_limit WHERE stamp<?", (now - 3600,))
            count = db.execute("SELECT COUNT(*) FROM rate_limit").fetchone()[0]
            last = db.execute("SELECT MAX(stamp) FROM rate_limit").fetchone()[0] or 0
            if count >= _MAX_PER_HOUR or now < float(last) + _API_LIMIT_SECONDS:
                db.execute("COMMIT")
                return False
            db.execute("INSERT INTO rate_limit(stamp) VALUES(?)", (now,))
            db.execute("COMMIT")
            return True
        except Exception:
            db.execute("ROLLBACK")
            raise


def _process_once(client, key):
    from method2_supply import create_individual_supply, _check_order_status
    from datetime import date
    plan = read_plan()
    if not plan:
        return
    jobs = plan["jobs"]

    verifying = next(
        (job for job in jobs if job.get("status") == "VERIFYING" and job.get("creation_draft_id")
         and time.time() - float(job.get("last_verify_epoch") or 0) > 30),
        None,
    )
    if verifying:
        verifying["last_verify_epoch"] = time.time()
        try:
            _check_order_status(client, key, verifying)
        finally:
            save_job(verifying)
        return

    if plan["stopped"]:
        return

    now = time.time()
    waiting = next((job for job in jobs if job["status"] == "WAITING"), None)
    retries = sorted(
        (job for job in jobs if job["status"] == "RETRY" and now >= float(job.get("next_retry_at") or 0)),
        key=lambda job: float(job.get("last_attempt_epoch") or 0),
    )
    job = waiting or (retries[0] if retries else None)
    if job is None or not _reserve_attempt():
        return

    job["last_attempt_epoch"] = time.time()
    job["status"] = "CHECKING"
    job["reason"] = "Проверяем Ozon"
    save_job(job)

    def checkpoint(current):
        # Persist SUBMITTING BEFORE non-idempotent supply creation.
        save_job(current)

    window = plan["window"]
    try:
        create_individual_supply(
            client, key, job,
            date.fromisoformat(window[0]), date.fromisoformat(window[1]),
            window[2], window[3],
            before_submit=checkpoint,
        )
    except Exception as exc:
        if job["status"] in ("SUBMITTING", "VERIFYING"):
            job["status"] = "NEEDS_REVIEW"
            job["reason"] = "Неясен результат создания: " + str(exc)[:150]
        else:
            job["status"] = "RETRY"
            job["reason"] = str(exc)[:150]
    finally:
        if job["status"] == "RETRY":
            job["next_retry_at"] = time.time() + _RETRY_EVERY
        save_job(job)


class _Runner:
    def __init__(self, client, key):
        self.client = client
        self.key = key
        self.thread = threading.Thread(target=self._run, daemon=True, name="ozon-method2-worker")
        self.thread.start()

    def _run(self):
        while True:
            try:
                _process_once(self.client, self.key)
            except Exception as ex:
                print("Ozon method 2 worker error:", repr(ex))
            time.sleep(15)


def ensure_worker(client, key):
    global _WORKER
    initialise()
    if not client or not key:
        return
    with _LOCK:
        if _WORKER is None or not _WORKER.thread.is_alive():
            _WORKER = _Runner(client, key)
