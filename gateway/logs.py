"""Logging setup: daily log files that are safe to tail on Windows.

Files are named <prefix>-YYYY-MM-DD.log and a new one is opened each day, so nothing is ever
renamed (renaming fails on Windows while someone tails the file with Get-Content -Wait).

    gateway-*.log  application events, admin audit trail, dependency health transitions
    access-*.log   one JSON object per HTTP request (public + admin)
    health-*.log   health transitions + periodic JSON snapshots of all dependencies
"""

import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from .config import Settings

ACCESS_LOGGER = "gateway.access"
HEALTH_LOGGER = "gateway.health"
SNAPSHOT_LOGGER = "gateway.health.snapshot"

TEXT_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


class DailyFileHandler(logging.FileHandler):
    def __init__(self, directory: Path, prefix: str, retention_days: int):
        self.directory = directory
        self.prefix = prefix
        self.retention_days = retention_days
        self._day = date.today()
        directory.mkdir(parents=True, exist_ok=True)
        super().__init__(self._path(self._day), encoding="utf-8", delay=True)
        self._purge()

    def _path(self, day: date) -> str:
        return str(self.directory / f"{self.prefix}-{day:%Y-%m-%d}.log")

    def emit(self, record: logging.LogRecord) -> None:
        day = date.fromtimestamp(record.created)
        if day != self._day:  # handler lock is held by Handler.handle()
            if self.stream:
                self.stream.close()
                self.stream = None
            self._day = day
            self.baseFilename = self._path(day)
            self._purge()
        super().emit(record)

    def _purge(self) -> None:
        cutoff = f"{self.prefix}-{self._day - timedelta(days=self.retention_days):%Y-%m-%d}.log"
        for f in self.directory.glob(f"{self.prefix}-????-??-??.log"):
            if f.name < cutoff:
                try:
                    f.unlink()
                except OSError:
                    pass


def setup_logging(settings: Settings) -> None:
    level = settings.log_level.upper()
    text = logging.Formatter(TEXT_FORMAT)
    raw = logging.Formatter("%(message)s")
    log_dir, keep = settings.log_dir, settings.log_retention_days

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(level)
    if settings.log_console:
        console = logging.StreamHandler(sys.stdout)
        console.setFormatter(text)
        root.addHandler(console)
    app_file = DailyFileHandler(log_dir, "gateway", keep)
    app_file.setFormatter(text)
    root.addHandler(app_file)

    access = logging.getLogger(ACCESS_LOGGER)
    access.propagate = False
    access.setLevel(logging.INFO)
    access_file = DailyFileHandler(log_dir, "access", keep)
    access_file.setFormatter(raw)
    access.addHandler(access_file)

    # Health transitions go to health-*.log AND gateway-*.log; snapshots only to health-*.log.
    health_file = DailyFileHandler(log_dir, "health", keep)
    health_file.setFormatter(text)
    logging.getLogger(HEALTH_LOGGER).addHandler(health_file)
    snapshot = logging.getLogger(SNAPSHOT_LOGGER)
    snapshot.propagate = False
    snapshot.setLevel(logging.INFO)
    snapshot.addHandler(health_file)

    # Never let client libraries log request details (could include auth headers).
    for name in ("httpx", "httpcore", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.WARNING)
