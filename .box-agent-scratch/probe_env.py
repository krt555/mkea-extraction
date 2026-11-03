"""Confirm the real .env was not modified and report the key source only."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from webapp.app import settings as st

print("real .env path:", st.ENV_PATH)
print("real .env exists:", st.ENV_PATH.exists())
if st.ENV_PATH.exists():
    names = []
    for line in st.ENV_PATH.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if text and not text.startswith("#") and "=" in text:
            names.append(text.split("=", 1)[0].strip())
    print("variable names in .env (values hidden):", names)

cfg = st.read_settings()
print("key_source:", cfg["key_source"], "| masked:", cfg["key_masked"])
print("MKEA_LLM_API_KEY present in os.environ:", bool(os.environ.get("MKEA_LLM_API_KEY")))
print("OPENCODE_GO_API_KEY present in os.environ:", bool(os.environ.get("OPENCODE_GO_API_KEY")))
