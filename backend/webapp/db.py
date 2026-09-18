"""
Accounts, sessions, subscriptions, organisations, credits, progress, API keys and work items for the CineCut web app.

On the studio PC everything lives in one SQLite file (data/cinecut.db); a hosted copy sets DATABASE_URL and uses
Postgres instead (see conn()). Passwords are stored as scrypt hashes; session cookies and
API keys are random tokens stored only as SHA-256 hashes. Credits are a ledger (every change has a reason and a
spend never takes a balance below zero); they are kept for later pricing, and everything is free for now.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Tuple

from backend.config import BASE_DIR

DB_PATH = BASE_DIR / "data" / "cinecut.db"
SIGNUP_CREDITS = int(os.environ.get("CINECUT_SIGNUP_CREDITS", "0"))
SESSION_DAYS = 30
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,24}$")
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT UNIQUE NOT NULL, name TEXT,
    pw_hash BLOB NOT NULL, pw_salt BLOB NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS user_country (user_id INTEGER PRIMARY KEY, country TEXT NOT NULL, updated REAL);
CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created REAL, expires REAL);
CREATE TABLE IF NOT EXISTS ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, delta INTEGER NOT NULL,
    reason TEXT NOT NULL, ref TEXT, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS progress (user_id INTEGER NOT NULL, title_id TEXT NOT NULL, position REAL, mode TEXT, updated REAL,
    PRIMARY KEY (user_id, title_id));
CREATE TABLE IF NOT EXISTS orders (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, plan_id TEXT, owner_type TEXT, owner_id INTEGER,
    seats INTEGER DEFAULT 1, amount_paise INTEGER, provider TEXT, provider_order_id TEXT, payment_id TEXT, status TEXT, created REAL, paid REAL);
CREATE TABLE IF NOT EXISTS subscriptions (id INTEGER PRIMARY KEY AUTOINCREMENT, owner_type TEXT NOT NULL, owner_id INTEGER NOT NULL,
    plan_id TEXT NOT NULL, stream TEXT NOT NULL, seats INTEGER DEFAULT 1, status TEXT NOT NULL, started REAL, until REAL, order_id TEXT);
CREATE TABLE IF NOT EXISTS orgs (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, kind TEXT NOT NULL, owner_id INTEGER NOT NULL,
    invite_code TEXT UNIQUE NOT NULL, languages TEXT, created REAL);
CREATE TABLE IF NOT EXISTS members (org_id INTEGER NOT NULL, user_id INTEGER NOT NULL, role TEXT NOT NULL, joined REAL,
    PRIMARY KEY (org_id, user_id));
CREATE TABLE IF NOT EXISTS org_titles (org_id INTEGER NOT NULL, title_id TEXT NOT NULL, added_by INTEGER, required INTEGER DEFAULT 0,
    created REAL, PRIMARY KEY (org_id, title_id));
CREATE TABLE IF NOT EXISTS completions (org_id INTEGER NOT NULL, user_id INTEGER NOT NULL, title_id TEXT NOT NULL, score INTEGER,
    total INTEGER, completed REAL, PRIMARY KEY (org_id, user_id, title_id));
CREATE TABLE IF NOT EXISTS api_keys (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT, key_hash TEXT UNIQUE NOT NULL,
    prefix TEXT, created REAL, revoked REAL DEFAULT 0, last_used REAL);
CREATE TABLE IF NOT EXISTS api_usage (id INTEGER PRIMARY KEY AUTOINCREMENT, key_id TEXT NOT NULL, endpoint TEXT, title_id TEXT,
    units REAL, created REAL);
CREATE TABLE IF NOT EXISTS work_items (id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, org_id INTEGER, kind TEXT NOT NULL,
    status TEXT NOT NULL, progress REAL DEFAULT 0, message TEXT, params TEXT, result TEXT, minutes REAL DEFAULT 0,
    created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS consents (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, kind TEXT NOT NULL,
    version TEXT NOT NULL, text_hash TEXT NOT NULL, basis TEXT, details TEXT, work_id TEXT, file_name TEXT,
    file_sha256 TEXT, file_bytes INTEGER, ip TEXT, agent TEXT, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS ledger_user ON ledger(user_id);
CREATE INDEX IF NOT EXISTS consents_user ON consents(user_id, created);
CREATE INDEX IF NOT EXISTS consents_work ON consents(work_id);
CREATE INDEX IF NOT EXISTS orders_provider ON orders(provider_order_id);
CREATE INDEX IF NOT EXISTS usage_key ON api_usage(key_id, created);
CREATE INDEX IF NOT EXISTS work_user ON work_items(user_id, created);
"""


# ------------------------------------------------------------------ storage: SQLite on the studio PC, Postgres when hosted
# A hosted server (Render's free plan) loses its disk on every deploy, and with it the accounts and the agreement records,
# so there DATABASE_URL points at a free Postgres (Neon). The SQL below is written once, in SQLite's dialect; _pg_sql()
# makes the few changes Postgres needs, and rows behave the same either way (row["col"], row[0], dict(row)).
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))
_pg: Dict[str, Any] = {"conn": None, "used": 0.0}


class _Row(dict):
    """A Postgres row that also answers row[0], like sqlite3.Row."""
    __slots__ = ("_values",)

    def __init__(self, cols, values):
        super().__init__(zip(cols, values))
        self._values = values

    def __getitem__(self, k):
        return self._values[k] if isinstance(k, int) else dict.__getitem__(self, k)


def _pg_rows(cursor):
    cols = [d.name for d in cursor.description] if cursor.description else []
    return lambda values: _Row(cols, values)


_PG_SWAPS = [("?", "%s"), ("MAX(completions.score, excluded.score)", "GREATEST(completions.score, excluded.score)")]


def _pg_sql(sql: str) -> str:
    for a, b in _PG_SWAPS:
        sql = sql.replace(a, b)
    return sql


def _pg_schema() -> str:
    return (SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
            .replace(" REAL", " DOUBLE PRECISION").replace(" BLOB", " BYTEA"))


class _PgConn:
    """The part of sqlite3.Connection this module uses, on a psycopg connection."""

    def __init__(self, c):
        self.c = c

    def execute(self, sql: str, params=()):
        cur = self.c.cursor()
        cur.execute(_pg_sql(sql), tuple(params))
        return cur

    def executescript(self, script: str) -> None:
        for stmt in script.split(";"):
            if stmt.strip():
                self.c.execute(stmt)


def _pg_connection():
    c = _pg["conn"]
    if c is not None and not c.closed and not c.broken and time.time() - _pg["used"] < 60:
        return c
    if c is not None and not c.closed and not c.broken:
        try:                                  # idle for a while: Neon may have closed it while scaled to zero
            c.execute("SELECT 1")
            c.commit()
            return c
        except Exception:
            pass
    try:
        if c is not None:
            c.close()
    except Exception:
        pass
    import psycopg
    c = psycopg.connect(DATABASE_URL, row_factory=_pg_rows, connect_timeout=20)
    _pg["conn"] = c
    return c


@contextmanager
def conn() -> Iterator[Any]:
    with _lock:
        if PG:
            c = _pg_connection()
            try:
                yield _PgConn(c)
                c.commit()
            except BaseException:
                if not c.broken:
                    c.rollback()
                raise
            finally:
                _pg["used"] = time.time()
            return
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(str(DB_PATH), timeout=10)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


def init() -> None:
    with conn() as c:
        if PG:
            c.executescript(_pg_schema())
            c.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS currency TEXT DEFAULT 'INR'")
            return
        c.executescript(SCHEMA)
        try:                                 # orders made before prices in dollars and pounds were rupees
            c.execute("ALTER TABLE orders ADD COLUMN currency TEXT DEFAULT 'INR'")
        except sqlite3.OperationalError:
            pass


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)


class AccountError(ValueError):
    pass


# ------------------------------------------------------------------ accounts
def _user(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if not row:
        return None
    return {"id": row["id"], "email": row["email"], "name": row["name"] or row["email"].split("@")[0], "created": row["created"]}


def create_user(email: str, password: str, name: str = "") -> Dict[str, Any]:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise AccountError("Enter a valid email address.")
    if len(password or "") < 8:
        raise AccountError("Use a password of at least 8 characters.")
    salt = secrets.token_bytes(16)
    now = time.time()
    with conn() as c:
        if c.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
            raise AccountError("An account with this email already exists. Log in instead.")
        uid = c.execute("INSERT INTO users (email, name, pw_hash, pw_salt, created) VALUES (?, ?, ?, ?, ?) RETURNING id",
                        (email, (name or "").strip()[:60], _hash(password, salt), salt, now)).fetchone()[0]
        if SIGNUP_CREDITS:
            c.execute("INSERT INTO ledger (user_id, delta, reason, created) VALUES (?, ?, 'welcome', ?)", (uid, SIGNUP_CREDITS, now))
        return _user(c.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())


def verify_login(email: str, password: str) -> Optional[Dict[str, Any]]:
    with conn() as c:
        row = c.execute("SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),)).fetchone()
    if not row:
        _hash(password or "x", b"0" * 16)        # same work either way, so timing does not reveal accounts
        return None
    return _user(row) if hmac.compare_digest(_hash(password or "", row["pw_salt"]), row["pw_hash"]) else None


def get_user(user_id: int) -> Optional[Dict[str, Any]]:
    with conn() as c:
        return _user(c.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())


def new_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with conn() as c:
        c.execute("DELETE FROM sessions WHERE expires < ?", (now,))
        c.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (_sha(token), user_id, now, now + SESSION_DAYS * 86400))
    return token


def user_for_session(token: Optional[str]) -> Optional[Dict[str, Any]]:
    if not token:
        return None
    with conn() as c:
        row = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires > ?",
                        (_sha(token), time.time())).fetchone()
    return _user(row)


def end_session(token: Optional[str]) -> None:
    if token:
        with conn() as c:
            c.execute("DELETE FROM sessions WHERE token_hash = ?", (_sha(token),))


# ------------------------------------------------------------------ credits (kept for later pricing)
def balance(user_id: int) -> int:
    with conn() as c:
        return int(c.execute("SELECT COALESCE(SUM(delta), 0) FROM ledger WHERE user_id = ?", (user_id,)).fetchone()[0])


def add_credits(user_id: int, amount: int, reason: str, ref: Optional[str] = None) -> int:
    with conn() as c:
        c.execute("INSERT INTO ledger (user_id, delta, reason, ref, created) VALUES (?, ?, ?, ?, ?)", (user_id, int(amount), reason, ref, time.time()))
    return balance(user_id)


def spend(user_id: int, amount: int, reason: str, ref: Optional[str] = None) -> bool:
    if amount <= 0:
        return True
    with conn() as c:
        have = int(c.execute("SELECT COALESCE(SUM(delta), 0) FROM ledger WHERE user_id = ?", (user_id,)).fetchone()[0])
        if have < amount:
            return False
        c.execute("INSERT INTO ledger (user_id, delta, reason, ref, created) VALUES (?, ?, ?, ?, ?)", (user_id, -int(amount), reason, ref, time.time()))
        return True


# ------------------------------------------------------------------ progress
def save_progress(user_id: int, title_id: str, position: float, mode: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO progress VALUES (?, ?, ?, ?, ?) ON CONFLICT(user_id, title_id) DO UPDATE SET "
                  "position = excluded.position, mode = excluded.mode, updated = excluded.updated",
                  (user_id, title_id, float(position), mode, time.time()))


def progress_for(user_id: int) -> Dict[str, Dict[str, Any]]:
    with conn() as c:
        return {r["title_id"]: {"position": r["position"], "mode": r["mode"], "updated": r["updated"]}
                for r in c.execute("SELECT * FROM progress WHERE user_id = ? ORDER BY updated DESC", (user_id,))}


# ------------------------------------------------------------------ orders and subscriptions
def create_order(user_id: int, plan_id: str, owner_type: str, owner_id: int, seats: int, amount_paise: int, provider: str,
                 currency: str = "INR") -> str:
    """amount_paise is in the currency's smallest unit (paise, cents or pence)."""
    oid = "ord_" + secrets.token_hex(8)
    with conn() as c:
        c.execute("INSERT INTO orders (id, user_id, plan_id, owner_type, owner_id, seats, amount_paise, provider, status, created, currency) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?)",
                  (oid, user_id, plan_id, owner_type, owner_id, seats, amount_paise, provider, time.time(), currency))
    return oid


def set_provider_order(order_id: str, provider_order_id: str) -> None:
    with conn() as c:
        c.execute("UPDATE orders SET provider_order_id = ? WHERE id = ?", (provider_order_id, order_id))


def get_order(order_id: str = "", provider_order_id: str = "") -> Optional[Dict[str, Any]]:
    with conn() as c:
        row = c.execute("SELECT * FROM orders WHERE id = ? OR (provider_order_id = ? AND ? != '')",
                        (order_id, provider_order_id, provider_order_id)).fetchone()
    return dict(row) if row else None


def mark_paid(order_id: str, payment_id: str = "") -> Tuple[Optional[Dict[str, Any]], bool]:
    """(order, newly_paid). An order is only ever paid once."""
    with conn() as c:
        row = c.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not row:
            return None, False
        newly = row["status"] != "paid"
        if newly:
            c.execute("UPDATE orders SET status = 'paid', paid = ?, payment_id = ? WHERE id = ?", (time.time(), payment_id, order_id))
    return get_order(order_id), newly


def orders_for(user_id: int) -> List[Dict[str, Any]]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT id, plan_id, owner_type, owner_id, seats, amount_paise, provider, status, created, paid "
                                           "FROM orders WHERE user_id = ? ORDER BY created DESC LIMIT 20", (user_id,))]


def add_subscription(owner_type: str, owner_id: int, plan_id: str, stream: str, seats: int, days: int, order_id: str) -> None:
    now = time.time()
    with conn() as c:
        cur = c.execute("SELECT id, until FROM subscriptions WHERE owner_type = ? AND owner_id = ? AND plan_id = ? AND status = 'active' "
                        "ORDER BY until DESC LIMIT 1", (owner_type, owner_id, plan_id)).fetchone()
        if cur and cur["until"] > now:          # renewal extends the current period
            c.execute("UPDATE subscriptions SET until = ?, seats = ?, order_id = ? WHERE id = ?",
                      (cur["until"] + days * 86400, max(seats, 1), order_id, cur["id"]))
        else:
            c.execute("INSERT INTO subscriptions (owner_type, owner_id, plan_id, stream, seats, status, started, until, order_id) "
                      "VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?)", (owner_type, owner_id, plan_id, stream, max(seats, 1), now, now + days * 86400, order_id))


def active_subscriptions(owner_type: str, owner_id: int) -> List[Dict[str, Any]]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT plan_id, stream, seats, started, until FROM subscriptions WHERE owner_type = ? "
                                           "AND owner_id = ? AND status = 'active' AND until > ?", (owner_type, owner_id, time.time()))]


# ------------------------------------------------------------------ organisations
def create_org(owner_id: int, name: str, kind: str, languages: str = "English,Hindi") -> Dict[str, Any]:
    if kind not in ("institute", "company"):
        raise AccountError("Choose institute or company.")
    name = (name or "").strip()[:80]
    if len(name) < 2:
        raise AccountError("Give the workspace a name.")
    code = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:8].upper()
    with conn() as c:
        oid = c.execute("INSERT INTO orgs (name, kind, owner_id, invite_code, languages, created) VALUES (?, ?, ?, ?, ?, ?) RETURNING id",
                        (name, kind, owner_id, code, languages, time.time())).fetchone()[0]
        c.execute("INSERT INTO members VALUES (?, ?, 'owner', ?)", (oid, owner_id, time.time()))
    return get_org(oid)


def get_org(org_id: int) -> Optional[Dict[str, Any]]:
    with conn() as c:
        row = c.execute("SELECT * FROM orgs WHERE id = ?", (org_id,)).fetchone()
        if not row:
            return None
        n = c.execute("SELECT COUNT(*) FROM members WHERE org_id = ?", (org_id,)).fetchone()[0]
    return dict(row, members=n)


def role_in(org_id: int, user_id: int) -> Optional[str]:
    with conn() as c:
        row = c.execute("SELECT role FROM members WHERE org_id = ? AND user_id = ?", (org_id, user_id)).fetchone()
    return row["role"] if row else None


def orgs_for_user(user_id: int) -> List[Dict[str, Any]]:
    with conn() as c:
        rows = c.execute("SELECT o.*, m.role FROM members m JOIN orgs o ON o.id = m.org_id WHERE m.user_id = ? ORDER BY o.created",
                         (user_id,)).fetchall()
    return [dict(r) for r in rows]


def join_org(user_id: int, invite_code: str) -> Dict[str, Any]:
    with conn() as c:
        row = c.execute("SELECT * FROM orgs WHERE invite_code = ?", ((invite_code or "").strip().upper(),)).fetchone()
        if not row:
            raise AccountError("That invite code was not found.")
        c.execute("INSERT INTO members VALUES (?, ?, 'member', ?) ON CONFLICT DO NOTHING", (row["id"], user_id, time.time()))
    return get_org(row["id"])


def org_members(org_id: int) -> List[Dict[str, Any]]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT u.id, u.email, u.name, m.role, m.joined FROM members m JOIN users u ON u.id = m.user_id "
                                           "WHERE m.org_id = ? ORDER BY m.joined", (org_id,))]


def set_role(org_id: int, user_id: int, role: str) -> None:
    if role not in ("admin", "member"):
        raise AccountError("Role must be admin or member.")
    with conn() as c:
        c.execute("UPDATE members SET role = ? WHERE org_id = ? AND user_id = ? AND role != 'owner'", (role, org_id, user_id))


def remove_member(org_id: int, user_id: int) -> None:
    with conn() as c:
        c.execute("DELETE FROM members WHERE org_id = ? AND user_id = ? AND role != 'owner'", (org_id, user_id))


def add_org_title(org_id: int, title_id: str, user_id: int, required: bool = False) -> None:
    with conn() as c:
        c.execute("INSERT INTO org_titles VALUES (?, ?, ?, ?, ?) ON CONFLICT(org_id, title_id) DO UPDATE SET "
                  "added_by = excluded.added_by, required = excluded.required, created = excluded.created",
                  (org_id, title_id, user_id, int(required), time.time()))


def remove_org_title(org_id: int, title_id: str) -> None:
    with conn() as c:
        c.execute("DELETE FROM org_titles WHERE org_id = ? AND title_id = ?", (org_id, title_id))


def org_title_ids(org_id: int) -> List[Dict[str, Any]]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT title_id, required, created FROM org_titles WHERE org_id = ? ORDER BY created DESC", (org_id,))]


def record_completion(org_id: int, user_id: int, title_id: str, score: int, total: int) -> None:
    with conn() as c:
        c.execute("INSERT INTO completions VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(org_id, user_id, title_id) DO UPDATE SET "
                  "score = MAX(completions.score, excluded.score), total = excluded.total, completed = excluded.completed",
                  (org_id, user_id, title_id, int(score), int(total), time.time()))


def completion_report(org_id: int) -> List[Dict[str, Any]]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT c.user_id, u.email, u.name, c.title_id, c.score, c.total, c.completed FROM completions c "
                                           "JOIN users u ON u.id = c.user_id WHERE c.org_id = ? ORDER BY c.completed DESC", (org_id,))]


# ------------------------------------------------------------------ partner API keys
def create_api_key(user_id: int, name: str) -> Dict[str, Any]:
    kid = "key_" + secrets.token_hex(6)
    plaintext = "cc_live_" + secrets.token_urlsafe(30)
    with conn() as c:
        c.execute("INSERT INTO api_keys (id, user_id, name, key_hash, prefix, created) VALUES (?, ?, ?, ?, ?, ?)",
                  (kid, user_id, (name or "API key").strip()[:60], _sha(plaintext), plaintext[:12], time.time()))
    return {"id": kid, "name": name, "key": plaintext, "prefix": plaintext[:12]}


def api_key_owner(plaintext: str) -> Optional[Dict[str, Any]]:
    if not plaintext:
        return None
    with conn() as c:
        row = c.execute("SELECT * FROM api_keys WHERE key_hash = ? AND revoked = 0", (_sha(plaintext),)).fetchone()
        if row:
            c.execute("UPDATE api_keys SET last_used = ? WHERE id = ?", (time.time(), row["id"]))
    return dict(row) if row else None


def api_keys_for(user_id: int) -> List[Dict[str, Any]]:
    with conn() as c:
        return [dict(r) for r in c.execute("SELECT id, name, prefix, created, revoked, last_used FROM api_keys WHERE user_id = ? "
                                           "ORDER BY created DESC", (user_id,))]


def revoke_api_key(user_id: int, key_id: str) -> None:
    with conn() as c:
        c.execute("UPDATE api_keys SET revoked = ? WHERE id = ? AND user_id = ?", (time.time(), key_id, user_id))


def log_api_usage(key_id: str, endpoint: str, title_id: Optional[str] = None, units: float = 1.0) -> None:
    with conn() as c:
        c.execute("INSERT INTO api_usage (key_id, endpoint, title_id, units, created) VALUES (?, ?, ?, ?, ?)",
                  (key_id, endpoint, title_id, units, time.time()))


def api_calls_since(key_id: str, seconds: float) -> int:
    with conn() as c:
        return int(c.execute("SELECT COUNT(*) FROM api_usage WHERE key_id = ? AND created > ?", (key_id, time.time() - seconds)).fetchone()[0])


def api_usage_report(user_id: int, days: int = 30) -> List[Dict[str, Any]]:
    since = time.time() - days * 86400
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT k.name AS key_name, u.endpoint, u.title_id, COUNT(*) AS calls, SUM(u.units) AS units FROM api_usage u "
            "JOIN api_keys k ON k.id = u.key_id WHERE k.user_id = ? AND u.created > ? GROUP BY k.name, u.endpoint, u.title_id "
            "ORDER BY calls DESC LIMIT 200", (user_id, since))]


# ------------------------------------------------------------------ work items (creator and workspace jobs)
def create_work(user_id: int, kind: str, params: Dict[str, Any], org_id: Optional[int] = None) -> str:
    wid = "wk_" + secrets.token_hex(6)
    now = time.time()
    with conn() as c:
        c.execute("INSERT INTO work_items (id, user_id, org_id, kind, status, message, params, created, updated) "
                  "VALUES (?, ?, ?, ?, 'queued', 'Waiting to start...', ?, ?, ?)", (wid, user_id, org_id, kind, json.dumps(params), now, now))
    return wid


def update_work(wid: str, **fields: Any) -> None:
    if "result" in fields and not isinstance(fields["result"], str):
        fields["result"] = json.dumps(fields["result"], ensure_ascii=False)
    fields["updated"] = time.time()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with conn() as c:
        c.execute(f"UPDATE work_items SET {cols} WHERE id = ?", (*fields.values(), wid))


def get_work(wid: str) -> Optional[Dict[str, Any]]:
    with conn() as c:
        row = c.execute("SELECT * FROM work_items WHERE id = ?", (wid,)).fetchone()
    if not row:
        return None
    d = dict(row)
    for k in ("params", "result"):
        try:
            d[k] = json.loads(d[k]) if d[k] else None
        except ValueError:
            pass
    return d


def work_for(user_id: int, org_id: Optional[int] = None, limit: int = 40) -> List[Dict[str, Any]]:
    with conn() as c:
        if org_id:
            ids = [r[0] for r in c.execute("SELECT id FROM work_items WHERE org_id = ? ORDER BY created DESC LIMIT ?", (org_id, limit))]
        else:
            ids = [r[0] for r in c.execute("SELECT id FROM work_items WHERE user_id = ? AND org_id IS NULL ORDER BY created DESC LIMIT ?",
                                           (user_id, limit))]
    return [get_work(i) for i in ids]


def minutes_used(owner_user: int, since: float) -> float:
    with conn() as c:
        return float(c.execute("SELECT COALESCE(SUM(minutes), 0) FROM work_items WHERE user_id = ? AND created > ? AND status = 'done'",
                               (owner_user, since)).fetchone()[0])


# ------------------------------------------------------------------ agreements (what the user accepted, and about what)
def record_consent(user_id: int, kind: str, version: str, text_hash: str, basis: str = "", details: str = "",
                   work_id: str = "", file_name: str = "", file_sha256: str = "", file_bytes: int = 0,
                   ip: str = "", agent: str = "") -> int:
    """Keeps one acceptance: the wording (by version and fingerprint), what was declared, and which exact file it was
    about. This is the evidence in a later dispute, so it is never changed or deleted with the file."""
    with conn() as c:
        row = c.execute("INSERT INTO consents (user_id, kind, version, text_hash, basis, details, work_id, file_name, "
                        "file_sha256, file_bytes, ip, agent, created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id",
                        (user_id, kind, version, text_hash, basis, details, work_id, file_name, file_sha256,
                         file_bytes, ip, agent[:300], time.time())).fetchone()
    return int(row[0])


def _consent_row(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["created_text"] = time.strftime("%Y-%m-%d %H:%M:%S %z", time.localtime(d["created"]))
    return d


def consents(user_id: int, kind: Optional[str] = None) -> List[Dict[str, Any]]:
    with conn() as c:
        rows = c.execute("SELECT c.*, u.email FROM consents c LEFT JOIN users u ON u.id = c.user_id WHERE c.user_id = ?"
                         + (" AND c.kind = ?" if kind else "") + " ORDER BY c.created DESC",
                         (user_id, kind) if kind else (user_id,)).fetchall()
    return [_consent_row(r) for r in rows]


def consent_for_work(work_id: str) -> Optional[Dict[str, Any]]:
    with conn() as c:
        row = c.execute("SELECT c.*, u.email FROM consents c LEFT JOIN users u ON u.id = c.user_id WHERE c.work_id = ? "
                        "ORDER BY c.created DESC LIMIT 1", (work_id,)).fetchone()
    return _consent_row(row) if row else None


# ------------------------------------------------------------------ country (whose copyright rules apply)
def user_country(user_id: int) -> Optional[str]:
    with conn() as c:
        row = c.execute("SELECT country FROM user_country WHERE user_id = ?", (user_id,)).fetchone()
    return row[0] if row else None


def set_user_country(user_id: int, country: str) -> None:
    with conn() as c:
        c.execute("INSERT INTO user_country (user_id, country, updated) VALUES (?, ?, ?) "
                  "ON CONFLICT(user_id) DO UPDATE SET country = excluded.country, updated = excluded.updated",
                  (user_id, country, time.time()))
