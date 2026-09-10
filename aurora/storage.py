import contextlib
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import time

from cryptography.fernet import Fernet


class Store:
    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self.db_path = self.directory / "aurora.db"
        self.jobs_path = self.directory / "tasks.db"
        key_path = self.directory / "master.key"
        if not key_path.exists():
            # Never silently generate a new key for an existing encrypted database.
            if self.db_path.exists() and self.db_path.stat().st_size:
                raise RuntimeError("master.key 丢失，请从同一份备份恢复；不能生成新密钥覆盖。")
            with open(key_path, "xb") as handle:
                handle.write(Fernet.generate_key())
            os.chmod(key_path, 0o600)
        os.chmod(key_path, 0o600)
        self.cipher = Fernet(key_path.read_bytes())
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS admins (
                    username TEXT PRIMARY KEY, password_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS login_attempts (ip TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS servers (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL,
                    port INTEGER NOT NULL, username TEXT NOT NULL, auth_type TEXT NOT NULL,
                    credential TEXT NOT NULL, passphrase TEXT NOT NULL DEFAULT '',
                    fingerprint TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL DEFAULT 'unchecked', last_error TEXT NOT NULL DEFAULT '',
                    last_checked REAL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS rules (
                    id TEXT PRIMARY KEY, server_id TEXT NOT NULL REFERENCES servers(id),
                    name TEXT NOT NULL, method TEXT NOT NULL,
                    protocol TEXT NOT NULL, listen_ip TEXT NOT NULL, listen_port INTEGER NOT NULL,
                    target_host TEXT NOT NULL, target_port INTEGER NOT NULL,
                    notes TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'stopped',
                    desired TEXT NOT NULL DEFAULT 'stopped', last_error TEXT NOT NULL DEFAULT '',
                    bytes_in INTEGER NOT NULL DEFAULT 0, bytes_out INTEGER NOT NULL DEFAULT 0,
                    raw_in INTEGER NOT NULL DEFAULT 0, raw_out INTEGER NOT NULL DEFAULT 0,
                    epoch TEXT NOT NULL DEFAULT '', sampled_at REAL,
                    created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS samples (
                    id INTEGER PRIMARY KEY, rule_id TEXT NOT NULL REFERENCES rules(id) ON DELETE CASCADE,
                    created REAL NOT NULL, bytes_in INTEGER NOT NULL, bytes_out INTEGER NOT NULL);
                CREATE INDEX IF NOT EXISTS sample_time ON samples(created);
                CREATE TABLE IF NOT EXISTS destinations (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL,
                    port INTEGER NOT NULL, notes TEXT NOT NULL DEFAULT '',
                    position INTEGER NOT NULL, created REAL NOT NULL);
            """)
            # Additive migration preserves existing nodes, rules, counters and credentials.
            for table in ('servers', 'rules'):
                columns = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
                if 'check_result' not in columns:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN check_result TEXT NOT NULL DEFAULT ''")
            db.execute("INSERT OR REPLACE INTO meta VALUES ('schema_version', '2')")
        with self.connect(jobs=True) as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, action TEXT NOT NULL, server_id TEXT NOT NULL,
                    rule_id TEXT, state TEXT NOT NULL DEFAULT 'pending',
                    log TEXT NOT NULL DEFAULT '', created REAL NOT NULL, started REAL, finished REAL);
                CREATE INDEX IF NOT EXISTS job_state ON jobs(state, created);
            """)
        for path in (self.db_path, self.jobs_path):
            os.chmod(path, 0o600)

    @contextlib.contextmanager
    def connect(self, jobs=False):
        db = sqlite3.connect(self.jobs_path if jobs else self.db_path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def rows(self, sql, params=(), jobs=False):
        with self.connect(jobs) as db:
            return [dict(row) for row in db.execute(sql, params)]

    def one(self, sql, params=(), jobs=False):
        rows = self.rows(sql, params, jobs)
        return rows[0] if rows else None

    def execute(self, sql, params=(), jobs=False):
        with self.connect(jobs) as db:
            db.execute(sql, params)

    def seal(self, value):
        return self.cipher.encrypt(value.encode()).decode() if value else ""

    def unseal(self, value):
        return self.cipher.decrypt(value.encode()).decode() if value else ""

    def sample(self, rule_id, incoming, outgoing, epoch):
        incoming, outgoing = max(0, int(incoming)), max(0, int(outgoing))
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
            if not row:
                return
            # A new boot/service generation starts a new counter. Persisted totals survive it.
            same = row["epoch"] == epoch and bool(epoch)
            delta_in = incoming - row["raw_in"] if same and incoming >= row["raw_in"] else incoming
            delta_out = outgoing - row["raw_out"] if same and outgoing >= row["raw_out"] else outgoing
            db.execute("""UPDATE rules SET bytes_in=bytes_in+?, bytes_out=bytes_out+?,
                raw_in=?, raw_out=?, epoch=?, sampled_at=? WHERE id=?""",
                (delta_in, delta_out, incoming, outgoing, epoch, now, rule_id))
            db.execute("INSERT INTO samples(rule_id,created,bytes_in,bytes_out) VALUES(?,?,?,?)",
                       (rule_id, now, delta_in, delta_out))
            db.execute("DELETE FROM samples WHERE created < ?", (now - 30 * 86400,))


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ":" + digest


def verify_password(password, encoded):
    return secrets.compare_digest(password_hash(password, encoded.split(":")[0]), encoded)


class ProcessLock:
    """Exactly one scheduler/API process may own a data directory."""
    def __init__(self, directory):
        self.path = Path(directory) / "process.lock"
        self.handle = None

    def acquire(self):
        self.handle = open(self.path, "a+b")
        if os.fstat(self.handle.fileno()).st_size == 0:
            self.handle.write(b"0")
            self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            raise RuntimeError("此数据目录已被另一个面板进程使用，请只启动一个实例。") from None

    def release(self):
        if self.handle:
            if os.name == "nt":
                import msvcrt
                self.handle.seek(0)
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_UNLCK, 1)
            self.handle.close()
            self.handle = None
