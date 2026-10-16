"""Dependency health monitor: Ollama, Windows services, GPU, disk.

Every HEALTH_INTERVAL_S it checks each dependency, logs state *changes* (e.g. "Cloudflared is
stopped" / "recovered"), and every HEALTH_SNAPSHOT_INTERVAL_S writes a full JSON snapshot to
health-*.log. The latest snapshot is served by GET /admin/status.
"""

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .db import utcnow
from .logs import HEALTH_LOGGER, SNAPSHOT_LOGGER

if TYPE_CHECKING:
    from .core import Gateway

log = logging.getLogger(HEALTH_LOGGER)
snapshot_log = logging.getLogger(SNAPSHOT_LOGGER)

GPU_QUERY = "name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw"
HEALTHY = ("ok", "absent")
REWARM_MIN_INTERVAL_S = 600.0


def gpu_stats() -> list[dict] | None:
    try:
        out = subprocess.run(
            ["nvidia-smi", f"--query-gpu={GPU_QUERY}", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0:
        return None
    gpus = []
    for line in out.stdout.strip().splitlines():
        name, util, used, total, temp, power = [p.strip() for p in line.split(",")]
        gpus.append({"name": name, "utilization_pct": _num(util), "memory_used_mib": _num(used),
                     "memory_total_mib": _num(total), "temperature_c": _num(temp), "power_w": _num(power)})
    return gpus


def _num(value: str) -> float | None:
    try:
        return float(value)
    except ValueError:
        return None


_SERVICE_STATES = {1: "stopped", 2: "start_pending", 3: "stop_pending", 4: "running",
                   5: "continue_pending", 6: "pause_pending", 7: "paused"}


def service_states(names: list[str]) -> dict[str, str]:
    """Query Windows service states through the Service Control Manager (no subprocess)."""
    if sys.platform != "win32" or not names:
        return {}
    import ctypes
    from ctypes import wintypes

    class SERVICE_STATUS(ctypes.Structure):
        _fields_ = [(f, wintypes.DWORD) for f in (
            "dwServiceType", "dwCurrentState", "dwControlsAccepted", "dwWin32ExitCode",
            "dwServiceSpecificExitCode", "dwCheckPoint", "dwWaitHint")]

    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    adv.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    adv.OpenSCManagerW.restype = wintypes.HANDLE
    adv.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
    adv.OpenServiceW.restype = wintypes.HANDLE
    adv.QueryServiceStatus.argtypes = [wintypes.HANDLE, ctypes.POINTER(SERVICE_STATUS)]
    adv.QueryServiceStatus.restype = wintypes.BOOL
    adv.CloseServiceHandle.argtypes = [wintypes.HANDLE]

    scm = adv.OpenSCManagerW(None, None, 0x0001)  # SC_MANAGER_CONNECT
    if not scm:
        return {n: "unknown" for n in names}
    states = {}
    try:
        for name in names:
            handle = adv.OpenServiceW(scm, name, 0x0004)  # SERVICE_QUERY_STATUS
            if not handle:
                states[name] = "not_installed" if ctypes.get_last_error() == 1060 else "unknown"
                continue
            try:
                status = SERVICE_STATUS()
                ok = adv.QueryServiceStatus(handle, ctypes.byref(status))
                states[name] = _SERVICE_STATES.get(status.dwCurrentState, "unknown") if ok else "unknown"
            finally:
                adv.CloseServiceHandle(handle)
    finally:
        adv.CloseServiceHandle(scm)
    return states


def disk_usage(paths: list[Path]) -> list[dict]:
    seen, disks = set(), []
    for p in paths:
        anchor = p.anchor or str(p)
        if anchor in seen or not os.path.exists(anchor):
            continue
        seen.add(anchor)
        u = shutil.disk_usage(anchor)
        disks.append({"path": anchor, "free_gb": round(u.free / 2**30, 1), "total_gb": round(u.total / 2**30, 1)})
    return disks


class HealthMonitor:
    def __init__(self, gw: "Gateway"):
        self.gw = gw
        self.last: dict[str, Any] | None = None
        self._states: dict[str, str] = {}
        # Startup already warms up: allow the first re-warm only after a 60 s grace period.
        self._last_rewarm = time.monotonic() - REWARM_MIN_INTERVAL_S + 60

    def _paths(self) -> list[Path]:
        s = self.gw.settings
        if s.monitor_paths.strip():
            return [Path(p.strip()) for p in s.monitor_paths.split(",") if p.strip()]
        paths = [s.db_path]
        if sys.platform == "win32":
            paths.append(Path(os.environ.get("SYSTEMDRIVE", "C:") + "\\"))
        return paths

    async def run(self) -> None:
        last_snapshot = 0.0
        while True:
            try:
                snap = await self.check()
                self._log_transitions(snap["components"])
                self._rewarm_if_unloaded(snap["ollama"])
                if time.monotonic() - last_snapshot >= self.gw.settings.health_snapshot_interval_s:
                    snapshot_log.info(json.dumps(snap, separators=(",", ":"), default=str))
                    last_snapshot = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("health check failed")
            await asyncio.sleep(self.gw.settings.health_interval_s)

    async def check(self) -> dict[str, Any]:
        s = self.gw.settings
        ollama, gpus, services, disks = await asyncio.gather(
            self._ollama(),
            asyncio.to_thread(gpu_stats),
            asyncio.to_thread(service_states, s.watch_service_list),
            asyncio.to_thread(disk_usage, self._paths()),
        )
        snap = {
            "ts": utcnow(),
            "components": self._evaluate(ollama, gpus, services, disks),
            "ollama": ollama,
            "gpu": gpus,
            "services": services,
            "disks": disks,
            "gateway": {
                "uptime_s": int(time.time() - self.gw.started_at),
                "concurrency": self.gw.gate.snapshot(),
                "pull_jobs_running": self.gw.pulls.running(),
            },
        }
        self.last = snap
        return snap

    async def _ollama(self) -> dict[str, Any]:
        try:
            version = await self.gw.ollama.version()
            loaded = [{"name": m["name"], "size": m.get("size"), "size_vram": m.get("size_vram"),
                       "context_length": m.get("context_length"), "expires_at": m.get("expires_at")}
                      for m in await self.gw.ollama.ps()]
            return {"reachable": True, "version": version, "loaded_models": loaded}
        except Exception as exc:
            return {"reachable": False, "error": str(exc)[:200] or type(exc).__name__}

    def _evaluate(self, ollama: dict, gpus: list | None, services: dict, disks: list) -> dict[str, dict]:
        s = self.gw.settings
        c: dict[str, dict] = {}
        if ollama["reachable"]:
            c["ollama"] = {"status": "ok", "detail": f"version {ollama['version']}"}
            partial = [m for m in ollama["loaded_models"] if m.get("size") and (m.get("size_vram") or 0) < m["size"]]
            if partial:
                pct = round(100 * (partial[0]["size_vram"] or 0) / partial[0]["size"])
                c["model_gpu"] = {"status": "warn", "detail": f"{partial[0]['name']} only {pct}% on GPU (slow)"}
            else:
                names = ", ".join(m["name"] for m in ollama["loaded_models"]) or "no model loaded"
                c["model_gpu"] = {"status": "ok", "detail": names}
        else:
            c["ollama"] = {"status": "down", "detail": ollama["error"]}

        for name, state in services.items():
            status = "ok" if state == "running" else "absent" if state == "not_installed" else "down"
            c[f"service:{name}"] = {"status": status, "detail": state}

        if not gpus:
            c["gpu"] = {"status": "warn", "detail": "nvidia-smi unavailable"}
        else:
            g = gpus[0]

            def fmt(value: float | None, spec: str, scale: float = 1) -> str:
                return "?" if value is None else format(value / scale, spec)

            detail = (f"{fmt(g['temperature_c'], '.0f')}C, {fmt(g['utilization_pct'], '.0f')}% util, "
                      f"{fmt(g['memory_used_mib'], '.1f', 1024)}/{fmt(g['memory_total_mib'], '.1f', 1024)} GB")
            hot = g["temperature_c"] is not None and g["temperature_c"] >= s.gpu_temp_warn_c
            c["gpu"] = {"status": "warn" if hot else "ok", "detail": detail}

        for d in disks:
            low = d["free_gb"] < s.disk_warn_gb
            c[f"disk:{d['path']}"] = {"status": "warn" if low else "ok", "detail": f"{d['free_gb']} GB free"}
        return c

    def _rewarm_if_unloaded(self, ollama: dict[str, Any]) -> None:
        """After an Ollama restart (crash, upgrade) nothing is loaded; reload before clients pay for it."""
        if not (self.gw.settings.warmup and ollama["reachable"]) or ollama["loaded_models"]:
            return
        if time.monotonic() - self._last_rewarm < REWARM_MIN_INTERVAL_S:
            return
        self._last_rewarm = time.monotonic()
        log.info("no model loaded in Ollama; warming up")
        self.gw.spawn(self.gw.warmup(), "warmup")

    def _log_transitions(self, components: dict[str, dict]) -> None:
        for name, comp in components.items():
            status, detail = comp["status"], comp["detail"]
            prev = self._states.get(name)
            if prev == status:
                continue
            self._states[name] = status
            if prev is None:
                log.log(logging.INFO if status in HEALTHY else logging.WARNING, "%s: %s (%s)", name, status, detail)
            elif status in HEALTHY:
                log.info("%s recovered: %s (%s), was %s", name, status, detail, prev)
            else:
                log.warning("%s is %s: %s, was %s", name, status, detail, prev)
