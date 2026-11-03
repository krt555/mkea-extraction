"""Temporary end-to-end probe for the new settings/mode endpoints."""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8137"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=30) as resp:
        return json.loads(resp.read().decode())


def send(path, method, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"}, method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


status = get("/api/status")
print("modes:", [(m["value"], m["short"], m["needs_llm"], m["speed"]) for m in status["modes"]])
print("llm available:", status["llm"]["available"])

cfg = get("/api/settings/llm")
print("has_key:", cfg["has_key"], "| presets:", len(cfg["presets"]), "| env exists:", cfg["env_file_exists"])
print("preset ids:", [p["id"] for p in cfg["presets"]])
print("model/base:", cfg["model"], cfg["base_url"])

print("test without key:", send("/api/settings/llm/test", "POST", {}))
print("save bad url:", send("/api/settings/llm", "PUT",
                            {"api_key": "k", "base_url": "ftp://x", "model": "m", "test": False}))
print("save blank model:", send("/api/settings/llm", "PUT",
                                {"api_key": "k", "base_url": "https://a.com/v1", "model": "", "test": False}))
print("bad mode job:", send("/api/jobs", "POST", {"paper_fk": 1, "mode": "nope"}))
print("bad mode batch:", send("/api/jobs/batch", "POST", {"paper_fks": [1], "mode": "nope"}))
print("index status:", urllib.request.urlopen(BASE + "/", timeout=30).status)
