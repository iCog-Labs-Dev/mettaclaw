import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OPENAI_API_KEY", "test-key")

import asyncio
import config_helper as ch


class _FakeCategoryScores:
    def __init__(self, scores): self._scores = scores
    def model_dump(self): return self._scores

class _FakeModerationResult:
    def __init__(self, scores): self.category_scores = _FakeCategoryScores(scores)

class _FakeModerationResponse:
    def __init__(self, scores): self.results = [_FakeModerationResult(scores)]

class _FakeModerations:
    def __init__(self, scores): self._scores = scores
    async def create(self, input): return _FakeModerationResponse(self._scores)

class _FakeModerationsNoCall:
    async def create(self, input):
        raise AssertionError("moderations.create should not be called")


def test_is_category_blocked_blocked_path():
    orig = ch.openai_client.moderations
    ch.openai_client.moderations = _FakeModerations({"violence": 0.95})
    try:
        result = asyncio.run(ch.is_category_blocked("I will hurt you"))
        assert result is True, result
    finally:
        ch.openai_client.moderations = orig


def test_is_category_blocked_allowed_path():
    orig = ch.openai_client.moderations
    scores = {"violence": 0.01, "harassment": 0.01, "hate": 0.01, "self-harm": 0.01, "sexual/minors": 0.01}
    ch.openai_client.moderations = _FakeModerations(scores)
    try:
        result = asyncio.run(ch.is_category_blocked("what a nice day"))
        assert result is False, result
    finally:
        ch.openai_client.moderations = orig


def test_is_category_blocked_empty_text_no_network():
    orig = ch.openai_client.moderations
    ch.openai_client.moderations = _FakeModerationsNoCall()
    try:
        assert asyncio.run(ch.is_category_blocked("")) is False
        assert asyncio.run(ch.is_category_blocked("   ")) is False
    finally:
        ch.openai_client.moderations = orig


def test_get_spam_protection_config_keys():
    config = ch.get_spam_protection_config()
    expected_keys = {"time_window", "message_limit", "cooldown_duration", "admin_alert_threshold"}
    assert set(config.keys()) == expected_keys, config


def test_get_spam_protection_config_defaults_when_absent():
    orig_cache = ch._config_cache
    ch._config_cache = {}
    try:
        config = ch.get_spam_protection_config()
        assert config == {
            "time_window": 10,
            "message_limit": 5,
            "cooldown_duration": 120,
            "admin_alert_threshold": 3,
        }, config
    finally:
        ch._config_cache = orig_cache


if __name__ == "__main__":
    test_is_category_blocked_blocked_path()
    test_is_category_blocked_allowed_path()
    test_is_category_blocked_empty_text_no_network()
    test_get_spam_protection_config_keys()
    test_get_spam_protection_config_defaults_when_absent()
    print("all config_helper tests passed")
