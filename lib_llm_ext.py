import os
import time
import hashlib
import logging
from typing import Optional, Tuple, Dict, Any

import openai

logger = logging.getLogger(__name__)

PROMPT_DELIMITER = ":-:-:-:"


def _log_raw(provider: str, model: str, raw: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
    print(f"[LLM_RAW] ts={ts} provider={provider} model={model} chars={len(raw or '')} raw={raw!r}")


def _split_system_user(content: str) -> Tuple[str, str]:
    """
    MeTTa sends:
        <system/context> :-:-:-: <last human/wakeup message>

    Keep the split intact so providers receive a real system/developer prompt
    instead of flattening everything into one user message.
    """
    content = content or ""

    if PROMPT_DELIMITER not in content:
        usermsg = content.strip()
        return "", usermsg or "EMPTY / NO NEW USER INPUT."

    sysmsg, _, usermsg = content.partition(PROMPT_DELIMITER)
    sysmsg = sysmsg.strip()
    usermsg = usermsg.strip()

    if not usermsg:
        usermsg = "EMPTY / NO NEW USER INPUT."

    return sysmsg, usermsg


def _stable_cache_key(provider: str, model: str, sysmsg: str) -> str:
    """
    Stable key for requests sharing the same system-prefix family.
    Do not include the user message here.
    """
    marker = " LAST_SKILL_USE_RESULTS: "
    stable = (sysmsg or "").split(marker, 1)[0].strip()
    digest = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]
    return f"{provider.lower()}:{model}:{digest}"


def _merge_dicts(base: Optional[Dict[str, Any]], extra: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(base or {})
    merged.update(extra or {})
    return merged


def _clean_text(text: str) -> str:
    """Unescape command placeholders and strip tool-call XML fragments."""
    return (text or "").replace("_quote_", '"').replace("_apostrophe_", "'") \
        .replace("</arg_value>", " ").replace("</tool_call>", " ") \
        .replace("<arg_value>", " ").replace("<tool_call>", " ")


def _get_media_helpers():
    """
    Import media helpers lazily so this module can still load in text-only
    deployments where src.media_handler is absent.
    """
    try:
        from src.media_handler import supports_vision, build_multimodal_content
        return supports_vision, build_multimodal_content
    except Exception as exc:
        logger.warning("media_handler unavailable; media will be described as unsupported: %s", exc)
        return None, None


def _media_unsupported_note(text: str) -> str:
    return (text or "") + "\n[Note: an image was attached but the current model does not support vision]"


def _media_describe_hint(text: str) -> str:
    return (text or "") + "\n[An image is attached — use the describe-image skill to read its contents]"


def _build_user_content_with_media(provider_name: str, usermsg: str, media=None):
    """
    Preserve the second file's multimodal behavior while keeping system/user
    prompt separation. Media is attached only to the user message.
    """
    if not media:
        return usermsg

    supports_vision, build_multimodal_content = _get_media_helpers()
    if supports_vision and build_multimodal_content:
        try:
            if supports_vision(provider_name):
                logger.info("[IMGDBG] attaching %d media part(s) to %s (vision)", len(media), provider_name)
                return build_multimodal_content(usermsg, media)
            # Provider is known and non-vision: point the agent at describe-image.
            logger.info("[IMGDBG] provider %s non-vision: dropping media, hinting describe-image", provider_name)
            return _media_describe_hint(usermsg)
        except Exception:
            logger.exception("Failed to build multimodal content for provider=%s", provider_name)

    return _media_unsupported_note(usermsg)


def _to_openai_responses_input(provider_name: str, usermsg: str, media=None):
    """
    Convert chat-completions-style multimodal content into Responses API input.
    If the media helper returns an unknown shape, fall back to text plus note.
    """
    if not media:
        return usermsg

    user_content = _build_user_content_with_media(provider_name, usermsg, media)
    if isinstance(user_content, str):
        return user_content

    if not isinstance(user_content, list):
        return _media_unsupported_note(usermsg)

    converted = []
    for part in user_content:
        if not isinstance(part, dict):
            continue

        part_type = part.get("type")
        if part_type in {"text", "input_text"}:
            converted.append({"type": "input_text", "text": part.get("text", "")})
            continue

        if part_type in {"image_url", "input_image"}:
            image_url = part.get("image_url")
            if isinstance(image_url, dict):
                image_url = image_url.get("url")
            if image_url:
                converted.append({"type": "input_image", "image_url": image_url})
            continue

    if not converted:
        return _media_unsupported_note(usermsg)

    return [{"role": "user", "content": converted}]


class AbstractAIProvider:
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def chat(self, content: str, max_tokens: int = 6000, reasoning: str = "medium", media=None, **kwargs) -> str:
        raise NotImplementedError

    @property
    def is_available(self) -> bool:
        raise NotImplementedError


class AIProvider(AbstractAIProvider):
    """Lazy OpenAI-compatible provider with on-demand initialization."""

    def __init__(self, name: str, var_name: str, model_name: str, base_url: str):
        super().__init__(name)
        self._var_name = var_name
        self._model_name = model_name
        self._base_url = base_url
        self._client = None

    def _ensure_client(self):
        if self._client is None:
            self._client = self._create_client()

    def _create_client(self) -> Optional[openai.OpenAI]:
        proxy_url = os.environ.get("GATEWAY_URL")
        if proxy_url:
            prefix = self._name.lower()
            base_url = f"{proxy_url.rstrip('/')}/{prefix}/"
            print(f"[lib_llm_ext.AIProvider._create_client] Connecting via proxy: {base_url}")
            return openai.OpenAI(api_key="proxy", base_url=base_url)

        if self._var_name in os.environ:
            if self._var_name == "OLLAMA_API_KEY":
                llm_server_local_url = os.environ.get("LLM_SERVER_LOCAL_URL")
                if llm_server_local_url:
                    self._base_url = llm_server_local_url.rstrip("/") + "/v1"
                elif not self._base_url.endswith("/v1"):
                    self._base_url = self._base_url.rstrip("/") + "/v1"

            return openai.OpenAI(api_key=os.environ.get(self._var_name), base_url=self._base_url)

        return None

    @property
    def is_available(self) -> bool:
        return bool(os.environ.get("GATEWAY_URL")) or bool(os.environ.get(self._var_name))

    def _build_messages(self, content: str, media=None):
        sysmsg, usermsg = _split_system_user(content)
        user_content = _build_user_content_with_media(self.name, usermsg, media)

        if sysmsg:
            return [
                {"role": "system", "content": sysmsg},
                {"role": "user", "content": user_content},
            ]

        return [{"role": "user", "content": user_content}]

    def chat(self, content: str, max_tokens: int = 6000, reasoning: str = "medium", media=None, **kwargs) -> str:
        self._ensure_client()

        if self._client is None:
            raise RuntimeError(f"{self.name} not configured (set {self._var_name})")

        try:
            response = self._client.chat.completions.create(
                model=self._model_name,
                messages=self._build_messages(content, media=media),
                max_tokens=max_tokens,
                **kwargs,
            )

            raw = response.choices[0].message.content or ""
            _log_raw(self._name, self._model_name, raw)
            return self._clean_text(raw)
        except Exception as e:
            print(f"[lib_llm_ext.AIProvider.chat] Exception while communicating with LLM: {e}")
            return ""

    def _clean_text(self, text: str) -> str:
        return _clean_text(text)


class OpenRouterProvider(AIProvider):
    """OpenRouter provider with reasoning mode enabled and excluded from response."""

    def _create_client(self) -> Optional[openai.OpenAI]:
        proxy_url = os.environ.get("GATEWAY_URL")
        if proxy_url:
            base_url = f"{proxy_url.rstrip('/')}/openrouter/"
            print(f"[lib_llm_ext.OpenRouterProvider._create_client] Connecting via proxy: {base_url}")
            return openai.OpenAI(api_key="proxy", base_url=base_url)

        if self._var_name in os.environ:
            return openai.OpenAI(api_key=os.environ.get(self._var_name), base_url=self._base_url)

        return None

    def _openrouter_extra_body(self, content: str, max_tokens: int) -> Dict[str, Any]:
        sysmsg, _ = _split_system_user(content)

        body = {
            "reasoning": {
                "enabled": True,
                "max_tokens": max_tokens,
                "exclude": True,
            }
        }

        session_id = os.environ.get("OPENROUTER_SESSION_ID")
        if not session_id and sysmsg:
            session_id = _stable_cache_key("openrouter", self._model_name, sysmsg)

        if session_id:
            body["session_id"] = session_id[:256]

        model = self._model_name.lower()
        if model.startswith("anthropic/"):
            body["cache_control"] = {
                "type": "ephemeral",
                "ttl": os.environ.get("OPENROUTER_CACHE_TTL", "5m"),
            }

        return body

    def chat(self, content: str, max_tokens: int = 6000, reasoning: str = "medium", media=None, **kwargs) -> str:
        extra_body = _merge_dicts(
            self._openrouter_extra_body(content, max_tokens),
            kwargs.pop("extra_body", None),
        )

        return super().chat(
            content=content,
            max_tokens=max_tokens,
            reasoning=reasoning,
            media=media,
            extra_body=extra_body,
            **kwargs,
        )


class AsiOneProvider(AIProvider):
    """ASI One provider with prompt separation and thinking enabled."""

    def chat(self, content: str, max_tokens: int = 6000, reasoning: str = "medium", media=None, **kwargs) -> str:
        self._ensure_client()

        if self._client is None:
            raise RuntimeError(f"{self.name} not configured (set {self._var_name})")

        sysmsg, usermsg = _split_system_user(content)
        user_content = _build_user_content_with_media(self.name, usermsg, media)

        messages = []
        if sysmsg:
            messages.append({"role": "system", "content": sysmsg})
        messages.append({"role": "user", "content": user_content})

        extra_body = _merge_dicts(
            {
                "enable_thinking": True,
                "thinking_budget": int(os.environ.get("ASIONE_THINKING_BUDGET", "6000")),
            },
            kwargs.pop("extra_body", None),
        )

        try:
            response = self._client.chat.completions.create(
                model=self._model_name,
                messages=messages,
                max_tokens=max_tokens,
                extra_body=extra_body,
                **kwargs,
            )

            raw = response.choices[0].message.content or ""
            _log_raw(self._name, self._model_name, raw)
            return self._clean_text(raw)
        except Exception as e:
            print(f"[lib_llm_ext.ASIOneProvider.chat] Exception while communicating with LLM: {e}")
            return ""


class OpenAIProvider(AIProvider):
    """OpenAI provider using the Responses API for reasoning models."""

    def chat(self, content: str, max_tokens: int = 6000, reasoning: str = "medium", media=None, **kwargs) -> str:
        self._ensure_client()

        if self._client is None:
            raise RuntimeError(f"{self.name} not configured (set {self._var_name})")

        sysmsg, usermsg = _split_system_user(content)
        input_payload = _to_openai_responses_input(self.name, usermsg, media=media)

        try:
            create_kwargs = {
                "instructions": sysmsg,
                "model": self._model_name,
                "input": input_payload,
                "max_output_tokens": max_tokens,
                "reasoning": {"effort": reasoning},
                "prompt_cache_key": os.environ.get(
                    "OPENAI_PROMPT_CACHE_KEY",
                    _stable_cache_key("openai", self._model_name, sysmsg),
                ),
            }

            if self._model_name.startswith(("gpt-5.5", "gpt-5.4")):
                create_kwargs["prompt_cache_retention"] = "24h"

            create_kwargs.update(kwargs)
            response = self._client.responses.create(**create_kwargs)

            usage = getattr(response, "usage", None)
            if usage:
                input_tokens = getattr(usage, "input_tokens", None)
                output_tokens = getattr(usage, "output_tokens", None)
                total_tokens = getattr(usage, "total_tokens", None)
                details = getattr(usage, "input_tokens_details", None)
                cached_tokens = getattr(details, "cached_tokens", None) if details else None

                print(
                    f"[LLM_USAGE] provider={self._name} model={self._model_name} "
                    f"input_tokens={input_tokens} output_tokens={output_tokens} "
                    f"total_tokens={total_tokens} cached_tokens={cached_tokens}"
                )

            raw = response.output_text or ""
            _log_raw(self._name, self._model_name, raw)
            return self._clean_text(raw)
        except Exception as e:
            print(f"[lib_llm_ext.OpenAIProvider.chat] Exception while communicating with LLM: {e}")
            return ""


class TestProvider(AbstractAIProvider):
    """Test provider for mocking LLM output."""

    def __init__(self):
        super().__init__("Test")
        self._mock = None
        self._controller_ip = os.environ.get("TEST_SERVER_IP") or os.environ.get("TEST_API_KEY")

    def _llm_mock(self):
        if not self._mock:
            try:
                from Autotests.mock.llm import LlmMockAgent, LLM_MOCK_PORT
                self._mock = LlmMockAgent((self._controller_ip, LLM_MOCK_PORT))
            except Exception:
                import Autotests.mock.rpc as rpc
                from Autotests.mock.llm import LlmMockAgent
                self._mock = LlmMockAgent((self._controller_ip, rpc.PORT_DEFAULT))
        return self._mock

    @property
    def is_available(self) -> bool:
        return self._controller_ip is not None

    def chat(self, content: str, max_tokens: int = 6000, reasoning: str = "medium", media=None, **kwargs) -> str:
        return self._llm_mock().chat(content)


_provider_registry: Dict[str, AbstractAIProvider] = {}


def _register_provider(name: str, var_name: str, model_name: str, base_url: str):
    _register_provider_instance(AIProvider(name, var_name, model_name, base_url))


def _register_provider_instance(provider: AbstractAIProvider):
    _provider_registry[provider.name] = provider


def _get_provider(name: str) -> Optional[AbstractAIProvider]:
    return _provider_registry.get(name)


# Register all providers lazily.
_register_provider(name="ASICloud", var_name="ASI_API_KEY", model_name="minimax/minimax-m3", base_url="https://inference.asicloud.cudos.org/v1")
_register_provider(name="Anthropic", var_name="ANTHROPIC_API_KEY", model_name="claude-opus-4-8", base_url="https://api.anthropic.com/v1/")
_register_provider(name="Ollama-local", var_name="OLLAMA_API_KEY", model_name="qwen3.5:9b", base_url="http://localhost:11434/v1")
_register_provider_instance(AsiOneProvider(name="ASIOne", var_name="ASIONE_API_KEY", model_name="asi1-ultra", base_url="https://api.asi1.ai/v1"))
_register_provider_instance(OpenRouterProvider(name="OpenRouter", var_name="OPENROUTER_API_KEY", model_name="z-ai/glm-5.2", base_url="https://openrouter.ai/api/v1"))
_register_provider_instance(OpenRouterProvider(name="MiniMaxM3", var_name="OPENROUTER_API_KEY", model_name="minimax/minimax-m3", base_url="https://openrouter.ai/api/v1"))
_register_provider_instance(OpenRouterProvider(name="OpenRouterVision", var_name="OPENROUTER_API_KEY", model_name=os.environ.get("VISION_MODEL", "anthropic/claude-haiku-4.5"), base_url="https://openrouter.ai/api/v1"))
_register_provider_instance(TestProvider())
_register_provider_instance(OpenAIProvider(name="OpenAI", var_name="OPENAI_API_KEY", model_name="gpt-5.5", base_url="https://api.openai.com/v1"))


def get_pending_context_block() -> str:
    """
    Format pending document context for prompt injection, or '' if none.
    This keeps the second file's [ATTACHED DOCUMENT CONTENT] behavior.
    """
    try:
        from src.media_handler import get_pending_context
        context = get_pending_context()
    except Exception as exc:
        logger.warning("Could not read pending context: %s", exc)
        return ""

    if not context:
        return ""
    return "\n\n[ATTACHED DOCUMENT CONTENT]\n" + context


def pending_media_count() -> int:
    """
    Number of pending out-of-band media blocks. Called from loop.metta to decide
    whether a turn must go through callProvider for vision/media handling.
    """
    try:
        from src.media_handler import get_pending_media
        media = get_pending_media()
    except Exception as exc:
        logger.warning("Could not read pending media: %s", exc)
        return 0

    return len(media) if media else 0


def callProvider(provider_name: str, content: str, max_tokens: int = 6000, reasoning: str = "medium", media=None) -> str:
    """
    Generic dispatcher for MeTTa.
    - Preserves prompt separation using PROMPT_DELIMITER.
    - Injects pending image/media blocks from src.media_handler when present.
    - Document/PDF text context is still injected upstream via get_pending_context_block().
    """
    provider = _get_provider(provider_name)
    if not provider or not provider.is_available:
        raise RuntimeError(f"Provider '{provider_name}' not available")

    if media is None:
        try:
            from src.media_handler import get_pending_media
            media = get_pending_media()
        except Exception:
            media = None

    return provider.chat(content=content, max_tokens=max_tokens, reasoning=reasoning, media=media)


# Backward-compatible helpers retained from older versions.
def _chatAsiOne(client, model, content, max_tokens=6000, **kwargs):
    sysmsg, usermsg = _split_system_user(content)
    messages = []
    if sysmsg:
        messages.append({"role": "system", "content": sysmsg})
    messages.append({"role": "user", "content": usermsg})

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            extra_body={
                "enable_thinking": True,
                "thinking_budget": 6000,
            },
            **kwargs,
        )
        return _clean_text(resp.choices[0].message.content or "")
    except Exception as e:
        print(f"[lib_llm_ext._chatAsiOne] Exception while communicating with LLM: {e}")
        return ""


def useAsi1(content):
    provider = _get_provider("ASIOne")
    if not provider or not provider.is_available:
        raise RuntimeError("Provider 'ASIOne' not available")
    return provider.chat(content=content)


_embedding_model = None


def initLocalEmbedding():
    model_name = "intfloat/e5-large-v2"
    global _embedding_model
    os.environ["HF_HUB_OFFLINE"] = "1"
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer(model_name)
    return _embedding_model


def useLocalEmbedding(atom):
    global _embedding_model
    if _embedding_model is None:
        raise RuntimeError("Call initLocalEmbedding() first.")
    return _embedding_model.encode(atom, normalize_embeddings=True).tolist()


def test_pointer_note():
    media = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}}]
    out = _build_user_content_with_media("OpenRouter", "hi", media)
    assert out == "hi\n[An image is attached — use the describe-image skill to read its contents]", out
    # No media -> untouched
    assert _build_user_content_with_media("OpenRouter", "hi", None) == "hi"

    global _get_media_helpers
    original_get_media_helpers = _get_media_helpers
    expected_unsupported = _media_unsupported_note("hi")
    try:
        # Case A: vision-capable provider whose build_multimodal_content raises.
        def _raising_build(usermsg, media):
            raise RuntimeError("boom")
        _get_media_helpers = lambda: (lambda name: True, _raising_build)
        assert _build_user_content_with_media("SomeVision", "hi", media) == expected_unsupported

        # Case B: media_handler absent (text-only deployment).
        _get_media_helpers = lambda: (None, None)
        assert _build_user_content_with_media("SomeVision", "hi", media) == expected_unsupported
    finally:
        _get_media_helpers = original_get_media_helpers

    print("test_pointer_note passed")


if __name__ == "__main__":
    test_pointer_note()
