"""Entrypoint: `python -m gateway` runs the public (8000) and admin (8001) servers in one process.

One process on purpose: rate limits and GPU slots are tracked in memory.
"""

import asyncio
import contextlib
import logging
import signal

import uvicorn

from .apps.admin import create_admin_app
from .apps.public import create_public_app
from .config import get_settings
from .core import Gateway
from .logs import setup_logging

log = logging.getLogger("gateway")


class _Server(uvicorn.Server):
    # Signals are handled once for both servers in main(), not per server.
    @contextlib.contextmanager
    def capture_signals(self):
        yield


async def main() -> None:
    settings = get_settings()
    setup_logging(settings)
    log.info("logs: %s (gateway-, access-, health-YYYY-MM-DD.log)", settings.log_dir)
    if not settings.admin_key:
        log.warning("ADMIN_KEY is empty: admin API is disabled (use `python -m gateway.cli` instead)")

    gw = Gateway(settings)
    await gw.start()

    common = dict(host=settings.bind_host, log_config=None, access_log=False, lifespan="off",
                  server_header=False, proxy_headers=False, timeout_graceful_shutdown=10)
    servers = [
        _Server(uvicorn.Config(create_public_app(gw), port=settings.public_port,
                               limit_concurrency=settings.limit_concurrency, **common)),
        _Server(uvicorn.Config(create_admin_app(gw), port=settings.admin_port, **common)),
    ]

    loop = asyncio.get_running_loop()

    def stop(*_):
        log.info("shutdown requested")
        # Release queued requests with a clean 503 now; running ones get timeout_graceful_shutdown.
        # Via the loop, because a signal handler may interrupt a scheduler state change.
        loop.call_soon_threadsafe(gw.scheduler.close)
        for s in servers:
            if s.should_exit:
                s.force_exit = True
            s.should_exit = True

    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGBREAK", None)):
        if sig is not None:
            signal.signal(sig, stop)

    log.info("public API on http://%s:%d  |  admin API on http://%s:%d",
             settings.bind_host, settings.public_port, settings.bind_host, settings.admin_port)
    try:
        await asyncio.gather(*(s.serve() for s in servers))
    finally:
        await gw.stop()


if __name__ == "__main__":
    asyncio.run(main())
