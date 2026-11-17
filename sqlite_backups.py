"""Verified SQLite snapshots; restoration tests never target the live database."""
import argparse
import asyncio
import json
import logging
import os
import re
import sqlite3
import tempfile
import time
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

KST = timezone(timedelta(hours=9))
AUTO = re.compile(r"jejuno-auto-\d{8}-\d{6}(?:-\d{6})?\.db\Z")
BACKUP = re.compile(r"jejuno-[a-zA-Z0-9_-]+\.db\Z")
TABLES = ("places", "users", "news_posts")
LOG = logging.getLogger(__name__)


class BackupBusy(Exception):
    pass


class BackupManager:
    def __init__(self, database):
        self.database = Path(database).resolve()
        self.root = self.database.parent / "backups"
        public_roots = (Path(__file__).resolve().parent / "static", self.database.parent / "uploads")
        if any(self.root.is_relative_to(path.resolve()) for path in public_roots):
            raise ValueError("Backups must not use a public directory")

    @contextmanager
    def lock(self):
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.is_symlink():
            raise ValueError("Backup directory must not be a symbolic link")
        with open(self.root / ".backup.lock", "a+b") as handle:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise BackupBusy() from exc
            try:
                yield
            finally:
                if os.name == "nt":
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle, fcntl.LOCK_UN)

    def path(self, name):
        if not BACKUP.fullmatch(name):
            raise ValueError("Invalid backup name")
        path = self.root / name
        if path.is_symlink() or path.resolve().parent != self.root or not path.is_file():
            raise ValueError("Backup not found")
        return path

    def files(self):
        if not self.root.exists() or self.root.is_symlink():
            return []
        files = []
        for path in self.root.glob("jejuno-*.db"):
            if BACKUP.fullmatch(path.name) and not path.is_symlink() and path.is_file():
                stat = path.stat()
                files.append(dict(name=path.name, size=stat.st_size, timestamp=stat.st_mtime,
                                  date=datetime.fromtimestamp(stat.st_mtime, KST).strftime("%Y-%m-%d %H:%M:%S"),
                                  kind="자동" if AUTO.fullmatch(path.name) else "수동" if path.name.startswith("jejuno-manual-") else "변경 전 / 기타"))
        return sorted(files, key=lambda row: (row["timestamp"], row["name"]), reverse=True)

    def metadata(self):
        try:
            return json.loads((self.root / ".backup-status.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def record(self, **values):
        # Under cross-process lock. No exception messages, secrets or DB rows.
        data = {**self.metadata(), **values}
        path = self.root / ".backup-status.tmp"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        path.chmod(0o600)
        path.replace(self.root / ".backup-status.json")

    @staticmethod
    def inspect(database):
        with closing(sqlite3.connect(Path(database).as_uri() + "?mode=ro", uri=True, timeout=30)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                raise ValueError("Database integrity check failed")
            counts = {table: conn.execute('SELECT count(*) FROM "' + table + '"').fetchone()[0] for table in TABLES}
            return dict(integrity="ok", counts=counts, journal_mode=conn.execute("PRAGMA journal_mode").fetchone()[0])

    @staticmethod
    def copy(source, destination):
        deadline = time.monotonic() + 120
        def progress(status, remaining, total):
            if time.monotonic() > deadline:
                raise TimeoutError("Backup exceeded time budget")
        with closing(sqlite3.connect(Path(source).as_uri() + "?mode=ro", uri=True, timeout=30)) as src:
            with closing(sqlite3.connect(destination, timeout=30)) as dst:
                src.backup(dst, pages=256, progress=progress, sleep=0.05)
                dst.execute("PRAGMA journal_mode=DELETE")

    def create(self, kind="manual", *, now=None):
        if kind not in ("auto", "manual"):
            raise ValueError("Invalid backup kind")
        now = now or datetime.now(KST)
        with self.lock():
            existing = [row for row in self.files() if AUTO.fullmatch(row["name"])]
            due = now.replace(hour=3, minute=0, second=0, microsecond=0)
            if now < due:
                due -= timedelta(days=1)
            if kind == "auto" and existing and existing[0]["timestamp"] >= due.timestamp():
                return None
            path = self.root / f"jejuno-{kind}-{now:%Y%m%d-%H%M%S}.db"
            if path.exists():
                path = self.root / f"jejuno-{kind}-{now:%Y%m%d-%H%M%S}-{now:%f}.db"
            partial = path.with_suffix(".partial")
            try:
                with partial.open("xb"):
                    pass
                partial.chmod(0o600)
                self.copy(self.database, partial)
                result = self.inspect(partial)
                with partial.open("r+b") as handle:
                    os.fsync(handle.fileno())
                if path.exists():
                    raise FileExistsError("Backup already exists")
                partial.rename(path)
                result.update(name=path.name, timestamp=now.timestamp(), size=path.stat().st_size, kind=kind)
                self.record(last_backup=result, last_error=None, **({"automatic_error": None} if kind == "auto" else {}))
                if kind == "auto":
                    for old in [row for row in self.files() if AUTO.fullmatch(row["name"])][14:]:
                        self.path(old["name"]).unlink()
                LOG.info("SQLite backup verified: kind=%s file=%s bytes=%d", kind, path.name, result["size"])
                return result
            except Exception as exc:
                if partial.exists():
                    partial.unlink()
                failure = dict(operation=kind, timestamp=now.timestamp(), error=type(exc).__name__)
                self.record(last_error=failure, **({"automatic_error": failure} if kind == "auto" else {}))
                LOG.error("SQLite backup failed: operation=%s error_type=%s", kind, type(exc).__name__)
                raise

    def verify_restore(self):
        with self.lock():
            try:
                rows = self.files()
                if not rows:
                    raise ValueError("No backup available")
                source = self.path(rows[0]["name"])
                before = self.inspect(source)
                with tempfile.TemporaryDirectory(prefix="jejuno-restore-") as directory:
                    restored = Path(directory) / "restore-test.db"
                    self.copy(source, restored)
                    result = self.inspect(restored)
                    if before["counts"] != result["counts"]:
                        raise ValueError("Restored counts differ")
                result.update(name=source.name, timestamp=time.time(), isolated=True)
                self.record(restore=result, restore_error=None)
                return result
            except Exception as exc:
                self.record(restore_error=dict(timestamp=time.time(), error=type(exc).__name__))
                LOG.error("SQLite restore verification failed: error_type=%s", type(exc).__name__)
                raise

    def status(self):
        files, metadata = self.files(), self.metadata()
        auto = next((row for row in files if AUTO.fullmatch(row["name"])), None)
        warning = not auto or time.time() - auto["timestamp"] > 36 * 3600 or bool(metadata.get("last_error") or metadata.get("automatic_error") or metadata.get("restore_error"))
        return dict(files=files, latest=files[0] if files else None, automatic=auto, warning=warning,
                    label="백업 확인 필요" if warning else "정상", **metadata)

    async def schedule(self):
        # Startup catches up missed 03:00 runs; lock + due check cover worker overlap.
        while True:
            try:
                await asyncio.to_thread(self.create, "auto")
            except BackupBusy:
                pass
            except Exception:
                LOG.error("Automatic SQLite backup needs operator attention")
            await asyncio.sleep(60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=os.environ.get("JEJUNO_DATABASE", str(Path(os.environ.get("JEJUNO_DATA_DIR", "data")) / "jejuno.db")))
    parser.add_argument("action", choices=("auto", "manual", "verify"))
    args = parser.parse_args()
    manager = BackupManager(args.database)
    print(json.dumps(manager.verify_restore() if args.action == "verify" else manager.create(args.action), ensure_ascii=False))
