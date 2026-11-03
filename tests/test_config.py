"""Tests for .env loading and extraction defaults."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from mkea_extract.config import ConfigError, LLMConfig, load_dotenv


class ConfigTests(TestCase):
    def test_dotenv_loading(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "# comment\nMKEA_LLM_MODEL=test-model\nMKEA_LLM_API_KEY='secret'\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {}, clear=True):
                loaded = load_dotenv(path)
                self.assertEqual(loaded, path)
                self.assertEqual(os.environ["MKEA_LLM_MODEL"], "test-model")
                self.assertEqual(os.environ["MKEA_LLM_API_KEY"], "secret")

    def test_defaults(self):
        with patch.dict(os.environ, {"MKEA_LLM_API_KEY": "k"}, clear=True):
            config = LLMConfig.from_env()
        self.assertEqual(config.model, "deepseek-v4.1-flash")
        self.assertEqual(config.base_url, "https://opencode.ai/inference/go/openai/v1")
        self.assertTrue(config.session_id)

    def test_missing_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.env"
            with patch.dict(os.environ, {"MKEA_ENV_FILE": str(missing)}, clear=True):
                with self.assertRaises(ConfigError):
                    LLMConfig.from_env()
                config = LLMConfig.from_env(require_key=False)
        self.assertEqual(config.api_key, "")

    def test_redacted(self):
        config = LLMConfig(api_key="secret")
        self.assertEqual(config.redacted()["api_key"], "***")
