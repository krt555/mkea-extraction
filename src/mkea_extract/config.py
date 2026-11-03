"""Runtime configuration for the extraction agent.

The project defaults to OpenCode Go's OpenAI-compatible endpoint and the
DeepSeek V4.1 Flash model.  Any OpenAI-compatible endpoint can be used by
changing the environment variables documented in ``.env.example``.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://opencode.ai/inference/go/openai/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash"


class ConfigError(ValueError):
    """Raised when the LLM configuration is incomplete."""


def _parse_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def find_dotenv(start: str | Path | None = None) -> Path | None:
    explicit = os.environ.get("MKEA_ENV_FILE")
    if explicit:
        path = Path(explicit).expanduser()
        return path if path.exists() else None
    current = Path(start or Path.cwd()).expanduser().resolve()
    if current.is_file():
        current = current.parent
    for directory in (current, *current.parents):
        candidate = directory / ".env"
        if candidate.exists():
            return candidate
    return None


def load_dotenv(path: str | Path | None = None, *, override: bool = False) -> Path | None:
    """Load the small ``.env`` subset used by this project."""

    env_path = Path(path).expanduser() if path else find_dotenv()
    if env_path is None or not env_path.exists():
        return None
    for line in env_path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("export "):
            text = text[len("export ") :].lstrip()
        if "=" not in text:
            continue
        key, raw = text.split("=", 1)
        key = key.strip()
        if not key or not key.replace("_", "").isalnum():
            continue
        if not override and key in os.environ:
            continue
        os.environ[key] = _parse_value(raw)
    return env_path


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


def _configure_ca_bundle() -> None:
    """Help Homebrew Python find the system CA bundle on macOS.

    ``urllib``/OpenSSL otherwise fail with CERTIFICATE_VERIFY_FAILED when the
    Python build does not ship a default ``cert.pem``.  An explicit
    ``SSL_CERT_FILE`` always wins.
    """

    if os.environ.get("SSL_CERT_FILE"):
        return
    for candidate in (
        "/etc/ssl/cert.pem",
        "/opt/homebrew/etc/ca-certificates/cert.pem",
        "/usr/local/etc/ca-certificates/cert.pem",
    ):
        if Path(candidate).exists():
            os.environ["SSL_CERT_FILE"] = candidate
            return


@dataclass
class LLMConfig:
    api_key: str
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    timeout_s: float = 300.0
    max_tokens: int = 65536
    temperature: float = 0.0
    max_retries: int = 3
    session_id: str = ""
    user_agent: str = "mkea-extract/0.1.0"

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if not self.session_id:
            self.session_id = os.environ.get("MKEA_LLM_SESSION_ID") or uuid.uuid4().hex

    @classmethod
    def from_env(
        cls,
        *,
        api_key_env: str = "MKEA_LLM_API_KEY",
        model: str | None = None,
        base_url: str | None = None,
        require_key: bool = True,
    ) -> "LLMConfig":
        load_dotenv()
        _configure_ca_bundle()
        key = os.environ.get(api_key_env, "") or os.environ.get("OPENCODE_GO_API_KEY", "")
        if require_key and not key:
            raise ConfigError(
                f"{api_key_env} is empty. Copy .env.example to .env and fill in your API key."
            )
        return cls(
            api_key=key,
            base_url=base_url or os.environ.get("MKEA_LLM_BASE_URL") or DEFAULT_BASE_URL,
            model=model or os.environ.get("MKEA_LLM_MODEL") or DEFAULT_MODEL,
            timeout_s=_env_float("MKEA_LLM_TIMEOUT", 300.0),
            max_tokens=_env_int("MKEA_LLM_MAX_TOKENS", 65536),
            temperature=_env_float("MKEA_LLM_TEMPERATURE", 0.0),
            max_retries=_env_int("MKEA_LLM_MAX_RETRIES", 3),
        )

    def redacted(self) -> dict[str, object]:
        return {
            "base_url": self.base_url,
            "model": self.model,
            "timeout_s": self.timeout_s,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "api_key": "***" if self.api_key else "",
        }
