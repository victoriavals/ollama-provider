from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Upstream Ollama (keep on loopback; never expose 11434 directly)
    ollama_base_url: str = "http://127.0.0.1:11434"
    upstream_connect_timeout: float = 5.0
    upstream_read_timeout: float = 300.0

    # Listeners: public (routed by Cloudflare Tunnel) and admin (local only)
    bind_host: str = "127.0.0.1"
    public_port: int = 8000
    admin_port: int = 8001
    admin_key: str = ""
    limit_concurrency: int = 64
    # Headers carrying the real client IP, set by the tunnel in front of port 8000
    # (Cloudflare: cf-connecting-ip; Tailscale Funnel / ngrok: x-forwarded-for, last entry used)
    client_ip_headers: str = "cf-connecting-ip,x-forwarded-for"

    # Storage
    db_path: Path = BASE_DIR / "data" / "gateway.db"
    usage_retention_days: int = 90
    # Comma-separated models added to the allowlist on first start
    seed_models: str = "gemma3:4b"

    # Per-key defaults (a key may override them)
    default_rpm: int = 60
    default_max_concurrent: int = 2

    # GPU capacity: keep equal to OLLAMA_NUM_PARALLEL
    global_concurrency: int = 4
    queue_timeout_s: float = 20.0
    stream_ping_interval_s: float = 15.0

    # Request limits
    max_tokens_cap: int = 2048
    max_body_bytes: int = 4_000_000
    max_messages: int = 100
    max_images: int = 2
    max_embedding_inputs: int = 256
    failed_auth_per_ip_per_min: int = 20

    warmup: bool = True

    # Logging: daily files in log_dir (gateway-, access-, health-YYYY-MM-DD.log)
    log_level: str = "INFO"
    log_dir: Path = BASE_DIR / "logs"
    log_retention_days: int = 30
    log_console: bool = True

    # Dependency health monitor
    health_interval_s: float = 30.0
    health_snapshot_interval_s: float = 300.0
    watch_services: str = "OllamaServe,Cloudflared"
    # Comma-separated paths whose drives are checked for free space (default: DB drive + system drive)
    monitor_paths: str = ""
    disk_warn_gb: float = 10.0
    gpu_temp_warn_c: float = 85.0

    @field_validator("db_path", "log_dir")
    @classmethod
    def _resolve_path(cls, v: Path) -> Path:
        return v if v.is_absolute() else BASE_DIR / v

    @property
    def client_ip_header_list(self) -> tuple[str, ...]:
        return tuple(h.strip().lower() for h in self.client_ip_headers.split(",") if h.strip())

    @property
    def watch_service_list(self) -> list[str]:
        return [s.strip() for s in self.watch_services.split(",") if s.strip()]

    @property
    def seed_model_list(self) -> list[str]:
        return [m.strip() for m in self.seed_models.split(",") if m.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
