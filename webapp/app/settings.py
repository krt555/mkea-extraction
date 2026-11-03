"""In-app LLM settings: read, validate and persist the API credentials.

The CLI reads its configuration from the project-root ``.env``.  Non-technical
users should never have to open a text file, so this module exposes the same
file as a small, safe API:

* reads are always redacted -- the key itself never leaves the process;
* writes patch only the three LLM keys and keep every other line intact;
* ``os.environ`` is updated in the same call, so a new key takes effect without
  restarting the server (``LLMConfig.from_env()`` re-reads the environment on
  every extraction).
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = ROOT / ".env"

KEY_VAR = "MKEA_LLM_API_KEY"
BASE_URL_VAR = "MKEA_LLM_BASE_URL"
MODEL_VAR = "MKEA_LLM_MODEL"
# from_env() also accepts this legacy variable; surface it so the UI can explain
# why a key appears configured even though .env looks empty.
LEGACY_KEY_VAR = "OPENCODE_GO_API_KEY"

DEFAULT_BASE_URL = "https://opencode.ai/inference/go/openai/v1"
DEFAULT_MODEL = "deepseek-v4.1-flash"

# Ready-made endpoints so users can pick instead of typing a URL.
PROVIDER_PRESETS: list[dict[str, str]] = [
    {
        "id": "opencode",
        "label": "OpenCode Go（项目默认）",
        "base_url": DEFAULT_BASE_URL,
        "model": DEFAULT_MODEL,
        "hint": "本项目默认服务，模型为 DeepSeek V4.1 Flash。",
    },
    {
        "id": "deepseek",
        "label": "DeepSeek 官方",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "hint": "在 platform.deepseek.com 的「API keys」页面创建密钥。",
    },
    {
        "id": "openai",
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
        "hint": "在 platform.openai.com 的「API keys」页面创建密钥。",
    },
    {
        "id": "dashscope",
        "label": "阿里云百炼（通义千问）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "hint": "在百炼控制台开通「兼容 OpenAI 模式」后获取密钥。",
    },
    {
        "id": "moonshot",
        "label": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-32k",
        "hint": "在 platform.moonshot.cn 创建密钥。",
    },
    {
        "id": "zhipu",
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-plus",
        "hint": "在 bigmodel.cn 的「API 密钥」页面获取。",
    },
    {
        "id": "ollama",
        "label": "本机 Ollama",
        "base_url": "http://localhost:11434/v1",
        "model": "qwen2.5:14b",
        "hint": "本机模型不校验密钥，密钥随便填一个非空值即可，例如 ollama。",
    },
    {
        "id": "custom",
        "label": "其他（手动填写）",
        "base_url": "",
        "model": "",
        "hint": "任何兼容 OpenAI 的服务都可以，地址填到 /v1 为止。",
    },
]


class SettingsError(ValueError):
    """Raised when the submitted settings cannot be accepted."""


def mask_key(key: str) -> str:
    """Show only enough of the key to recognise it (never the secret itself)."""
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:3]}{'*' * 6}{key[-4:]}"


def _strip_quotes(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value


def _read_env_file() -> dict[str, str]:
    """Parse the subset of .env that this app manages."""
    values: dict[str, str] = {}
    if not ENV_PATH.exists():
        return values
    try:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        if text.startswith("export "):
            text = text[len("export ") :].lstrip()
        key, raw = text.split("=", 1)
        values[key.strip()] = _strip_quotes(raw)
    return values


def _patch_env_file(updates: dict[str, str]) -> None:
    """Rewrite only the managed keys, preserving comments and other settings."""
    existing = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    if not existing:
        existing = [
            "# MKEA LLM configuration (written by the in-app settings panel).",
            "# This file is gitignored; never commit real keys.",
        ]

    remaining = dict(updates)
    output: list[str] = []
    for line in existing:
        stripped = line.strip()
        matched = None
        if stripped and not stripped.startswith("#") and "=" in stripped:
            candidate = stripped[len("export ") :].lstrip() if stripped.startswith("export ") else stripped
            name = candidate.split("=", 1)[0].strip()
            if name in remaining:
                matched = name
        if matched is not None:
            output.append(f"{matched}={remaining.pop(matched)}")
        else:
            output.append(line)

    for name, value in remaining.items():
        output.append(f"{name}={value}")

    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = ENV_PATH.with_suffix(".env.tmp")
    tmp.write_text("\n".join(output).rstrip("\n") + "\n", encoding="utf-8")
    tmp.replace(ENV_PATH)


def _effective_key() -> tuple[str, str]:
    """Return (key, source) for the key that from_env() would actually use.

    ``from_env()`` loads ``.env`` into ``os.environ`` before reading it, so the
    file counts as configured even when the variable is not exported yet.  The
    settings panel can be opened before any extraction has run, so the file
    must be consulted directly instead of trusting the environment alone.
    """
    file_values = _read_env_file()
    env_key = os.environ.get(KEY_VAR, "")
    file_key = file_values.get(KEY_VAR, "")
    if env_key:
        return env_key, "env" if env_key != file_key else "file"
    if file_key:
        return file_key, "file"
    legacy = os.environ.get(LEGACY_KEY_VAR, "")
    if legacy:
        return legacy, "legacy"
    return "", "none"


def read_settings() -> dict[str, Any]:
    """Current configuration, with the key redacted."""
    file_values = _read_env_file()
    key, source = _effective_key()
    base_url = (
        os.environ.get(BASE_URL_VAR)
        or file_values.get(BASE_URL_VAR)
        or DEFAULT_BASE_URL
    )
    model = os.environ.get(MODEL_VAR) or file_values.get(MODEL_VAR) or DEFAULT_MODEL
    return {
        "has_key": bool(key),
        "key_masked": mask_key(key),
        "key_source": source,
        "base_url": base_url,
        "model": model,
        "env_file": str(ENV_PATH),
        "env_file_exists": ENV_PATH.exists(),
        "presets": PROVIDER_PRESETS,
    }


def _validate_base_url(base_url: str) -> str:
    url = base_url.strip().rstrip("/")
    if not url:
        raise SettingsError("接口地址不能为空")
    if not re.match(r"^https?://", url, flags=re.I):
        raise SettingsError("接口地址必须以 http:// 或 https:// 开头")
    if url.endswith("/chat/completions"):
        raise SettingsError("接口地址请填到 /v1 为止，不要带 /chat/completions")
    return url


def save_settings(
    *,
    api_key: str | None,
    base_url: str,
    model: str,
) -> dict[str, Any]:
    """Persist settings to .env and apply them to the running process."""
    url = _validate_base_url(base_url)
    name = model.strip()
    if not name:
        raise SettingsError("模型名称不能为空")

    updates = {BASE_URL_VAR: url, MODEL_VAR: name}

    # An omitted/blank key means "keep the stored one", so users can change the
    # model without re-typing the secret.
    key = (api_key or "").strip()
    if key:
        if any(ch.isspace() for ch in key):
            raise SettingsError("密钥中不应包含空格或换行")
        updates[KEY_VAR] = key

    _patch_env_file(updates)
    for variable, value in updates.items():
        os.environ[variable] = value
    if key:
        # A stale legacy variable would otherwise keep shadowing the new key.
        os.environ.pop(LEGACY_KEY_VAR, None)
    return read_settings()


def clear_key() -> dict[str, Any]:
    """Remove the stored key and fall back to the rule engine."""
    _patch_env_file({KEY_VAR: ""})
    os.environ.pop(KEY_VAR, None)
    os.environ.pop(LEGACY_KEY_VAR, None)
    return read_settings()


def test_connection(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Send one minimal request to verify endpoint, key and model.

    Candidate values are tested without being saved, so the panel can validate
    before persisting anything.
    """
    import sys

    src = str(ROOT / "src")
    if (ROOT / "src").is_dir() and src not in sys.path:
        sys.path.insert(0, src)

    from mkea_extract.config import ConfigError, LLMConfig
    from mkea_extract.llm import LLMError, OpenAICompatibleLLM

    current = read_settings()
    key = (api_key or "").strip()
    if not key:
        key, _ = _effective_key()
    if not key:
        return {"ok": False, "message": "还没有填写 API Key"}

    try:
        url = _validate_base_url(base_url or current["base_url"])
    except SettingsError as exc:
        return {"ok": False, "message": str(exc)}
    name = (model or "").strip() or current["model"]

    try:
        config = LLMConfig(api_key=key, base_url=url, model=name)
    except ConfigError as exc:
        return {"ok": False, "message": str(exc)}

    try:
        response = OpenAICompatibleLLM(config).check()
    except LLMError as exc:
        return {"ok": False, "message": _friendly_error(str(exc))}
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "message": f"连接失败：{exc}"}

    return {
        "ok": True,
        "message": f"连接成功：{response.model} · 用时 {response.latency_s:.1f} 秒",
        "model": response.model,
        "latency_s": round(response.latency_s, 2),
    }


def _friendly_error(raw: str) -> str:
    """Translate the most common endpoint failures into actionable Chinese."""
    text = raw.strip()
    lowered = text.lower()
    if "authentication failed" in lowered or "401" in lowered or "403" in lowered:
        return "密钥被拒绝（401/403）：请检查 API Key 是否正确、是否已过期或欠费。"
    if "404" in lowered:
        return "接口地址或模型名不存在（404）：确认地址填到 /v1 为止，且模型名拼写正确。"
    if "429" in lowered:
        return "请求过于频繁或额度用尽（429）：稍后再试或检查账户额度。"
    if "network error" in lowered or "timed out" in lowered or "timeout" in lowered:
        return "网络无法连通：检查接口地址、本机网络或代理设置。"
    if "not return json" in lowered or "not with json" in lowered:
        return "服务已连通但未返回 JSON：该模型可能不支持结构化输出，建议换一个模型。"
    return f"连接失败：{text[:300]}"


__all__ = [
    "PROVIDER_PRESETS",
    "SettingsError",
    "clear_key",
    "read_settings",
    "save_settings",
    "test_connection",
]
