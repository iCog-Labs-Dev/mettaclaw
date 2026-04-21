from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

pytest.importorskip("openai")

import config_helper


def test_telegram_tool_policy_blocks_configured_tools():
    original = config_helper._config_cache
    try:
        config_helper._config_cache = {
            "telegram": {
                "tool_policy": {
                    "disable_shell": True,
                    "disable_write_file": True,
                    "disable_append_file": True,
                    "disable_metta_eval": True,
                }
            }
        }
        assert config_helper.is_tool_blocked_in_telegram("shell") is True
        assert config_helper.is_tool_blocked_in_telegram("write-file") is True
        assert config_helper.is_tool_blocked_in_telegram("append-file") is True
        assert config_helper.is_tool_blocked_in_telegram("metta") is True
        assert config_helper.is_tool_blocked_in_telegram("read-file") is False
    finally:
        config_helper._config_cache = original
