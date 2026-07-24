import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import openrouter_vision as ov


class _FakeMessage:
    def __init__(self, content): self.content = content

class _FakeChoice:
    def __init__(self, content): self.message = _FakeMessage(content)

class _FakeResp:
    def __init__(self, content): self.choices = [_FakeChoice(content)]

class _FakeCompletions:
    def __init__(self, captured): self._captured = captured
    def create(self, model, messages, **kwargs):
        self._captured["model"] = model
        self._captured["messages"] = messages
        return _FakeResp("a red panda")

class _FakeChat:
    def __init__(self, captured): self.completions = _FakeCompletions(captured)

class _FakeClient:
    def __init__(self, captured): self.chat = _FakeChat(captured)


def test_vision_chat_builds_multimodal_request(monkeypatch=None):
    captured = {}
    os.environ["OPENROUTER_API_KEY"] = "test-key"
    orig = ov._make_client
    ov._make_client = lambda: _FakeClient(captured)
    try:
        parts = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}]
        out = ov.vision_chat(parts, "describe it")
        assert out == "a red panda", out
        content = captured["messages"][0]["content"]
        assert content[0] == {"type": "text", "text": "describe it"}, content
        assert content[1] == parts[0], content
    finally:
        ov._make_client = orig


def test_vision_chat_requires_key():
    orig_key = os.environ.pop("OPENROUTER_API_KEY", None)
    try:
        raised = False
        try:
            ov.vision_chat([{"type": "image_url", "image_url": {"url": "x"}}], "p")
        except RuntimeError:
            raised = True
        assert raised, "expected RuntimeError when key missing"
    finally:
        if orig_key is not None:
            os.environ["OPENROUTER_API_KEY"] = orig_key


if __name__ == "__main__":
    test_vision_chat_builds_multimodal_request()
    test_vision_chat_requires_key()
    print("all openrouter_vision tests passed")
