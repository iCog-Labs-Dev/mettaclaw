import os, openai
from typing import Optional
import logging

logger = logging.getLogger(__name__)

class AbstractAIProvider:
    def __init__(self, name: str):
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def chat(self, model: str, content: str, max_tokens: int = 6000, **kwargs) -> str:
        raise NotImplementedError

    @property
    def is_available(self) -> bool:
        raise NotImplementedError

class AIProvider(AbstractAIProvider):
    """Lazy AI provider with on-demand initialization."""

    def __init__(self, name: str, var_name: str, model_name: str, base_url: str):
        super().__init__(name)
        self._var_name = var_name
        self._model_name = model_name
        self._base_url = base_url
        self._client = None  # lazy initialization

    def _ensure_client(self):
        """Initialize client on first use."""
        if self._client is None:
            self._client = self._create_client()

    def _create_client(self) -> Optional[openai.OpenAI]:
        """Create OpenAI client from environment."""
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
        """Check if provider is configured (without initializing)."""
        return bool(os.environ.get(self._var_name))

    def chat(self, content: str, max_tokens: int = 6000, media=None, **kwargs) -> str:
        """Send chat request, initializing client if needed."""
        self._ensure_client()

        if self._client is None:
            raise RuntimeError(f"{self.name} not configured (set {self._var_name})")

        content = content.replace(":-:-:-:", " ")

        if media:
            from src.media_handler import supports_vision, build_multimodal_content
            if supports_vision(self.name):
                user_content = build_multimodal_content(content, media)
            else:
                content = content + "\n[Note: an image was attached but the current model does not support vision]"
                user_content = content
        else:
            user_content = content

        # OpenAI's current models reject `max_tokens` on chat.completions and
        # require `max_completion_tokens`; the OpenAI-compatible providers
        # (Anthropic, ASICloud, Ollama) still expect `max_tokens`.
        token_param = "max_completion_tokens" if "api.openai.com" in self._base_url else "max_tokens"

        try:
            response = self._client.chat.completions.create(
                model=self._model_name,
                messages=[{"role": "user", "content": user_content}],
                **{token_param: max_tokens},
                **kwargs
            )

            return self._clean_text(response.choices[0].message.content)
        except Exception as e:
            print(f"[lib_llm_ext.AIProvider.chat] Exception while communicating with LLM: {e}")
            return ""

    def _clean_text(self, text: str) -> str:
        """Unescape special characters."""
        return text.replace("_quote_", '"').replace("_apostrophe_", "'")

class OpenRouterProvider(AIProvider):
    def chat(self, content: str, max_tokens: int = 6000, media=None, **kwargs) -> str:
        self._ensure_client()

        if self._client is None:
            raise RuntimeError(f"{self.name} not configured (set {self._var_name})")

        if media:
            content = content + "\n[Note: an image was attached but the current model does not support vision]"

        try:
            response = self._client.chat.completions.create(
                model=self._model_name,
                messages=[{"role": "user", "content": content}],
                max_tokens=max_tokens,
                extra_body={
                    "reasoning": {
                        "enabled": True,
                        "max_tokens": 6000,
                        "exclude": True,
                    }
                },
                **kwargs
            )

            msg = response.choices[0].message
            final = msg.content or ""

            return self._clean_text(final)

        except Exception as e:
            logger.exception("[OpenRouterProvider.chat] OpenRouter request failed")
            return ""

class AsiOneProvider(AIProvider):
    """Lazy AI provider with on-demand initialization."""

    def __init__(self, name: str, var_name: str, model_name: str, base_url: str):
        super().__init__(name, var_name, model_name, base_url)

    def chat(self, content: str, max_tokens: int = 6000, media=None, **kwargs) -> str:
        """Send chat request, initializing client if needed."""
        self._ensure_client()

        if self._client is None:
            raise RuntimeError(f"{self.name} not configured (set {self._var_name})")

        sysmsg, usermsg = content.split(":-:-:-:")
        if media:
            usermsg = usermsg + "\n[Note: an image was attached but the current model does not support vision]"

        try:
            response = self._client.chat.completions.create(
                model=self._model_name,
                messages=[{"role": "system", "content": sysmsg},
                          {"role": "user", "content": usermsg}],
                max_tokens=max_tokens,
                extra_body={
                    "enable_thinking": True,
                    "thinking_budget": 6000
                },
                **kwargs
            )

            return self._clean_text(response.choices[0].message.content)
        except Exception as e:
            print(f"[lib_llm_ext.ASIOneProvider.chat] Exception while communicating with LLM: {e}")
            return ""

class TestProvider(AbstractAIProvider):
    """Test provider for mocking LLM output"""

    def __init__(self):
        super().__init__("Test")
        self._mock = None
        self._controller_ip = os.environ.get("TEST_API_KEY")

    def _llm_mock(self):
        if not self._mock:
            import Autotests.mock.rpc as rpc
            from Autotests.mock.llm import LlmMockAgent
            self._mock = LlmMockAgent((self._controller_ip, rpc.PORT_DEFAULT))
        return self._mock

    @property
    def is_available(self) -> bool:
        return self._controller_ip is not None

    def chat(self, content: str, max_tokens: int = 6000, **kwargs) -> str:
        return self._llm_mock().chat(content)

# Provider registry - lazy, no initialization yet
_provider_registry = {}


def _register_provider(name: str, var_name: str, model_name: str, base_url: str):
    """Register a provider configuration (no instantiation yet)."""
    _register_provider_instance(AIProvider(name, var_name, model_name, base_url))

def _register_provider_instance(provider: AbstractAIProvider):
    """Register a pre-initialized provider configuration (no instantiation yet)."""
    _provider_registry[provider.name] = provider

def _get_provider(name: str) -> Optional[AIProvider]:
    """Get or create provider instance on demand."""
    return _provider_registry.get(name)


# Register all providers (cheap - just stores config)
_register_provider(name="ASICloud", var_name="ASI_API_KEY", model_name="minimax/minimax-m2.5", base_url="https://inference.asicloud.cudos.org/v1")
_register_provider(name="Anthropic", var_name="ANTHROPIC_API_KEY", model_name="claude-opus-4-6", base_url="https://api.anthropic.com/v1/")
_register_provider(name="Ollama-local", var_name="OLLAMA_API_KEY", model_name="qwen3.5:9b", base_url="http://localhost:11434/v1")
_register_provider_instance(AsiOneProvider(name="ASIOne", var_name="ASIONE_API_KEY", model_name="asi1-ultra", base_url="https://api.asi1.ai/v1"))
_register_provider_instance(OpenRouterProvider(name="OpenRouter", var_name="OPENROUTER_API_KEY", model_name="z-ai/glm-5.1", base_url="https://openrouter.ai/api/v1"))
# _register_provider(name="OpenRouter", var_name="OPENROUTER_API_KEY", model_name="z-ai/glm-5.1", base_url="https://openrouter.ai/api/v1")
_register_provider_instance(TestProvider())
# At the moment the OpenAI model call is in PeTTa, just init a default config here
_register_provider(name="OpenAI", var_name="OPENAI_API_KEY", model_name="gpt-5.4", base_url="https://api.openai.com/v1")


def get_pending_context_block() -> str:
    """Format the pending document context for prompt injection, or '' if none.
    Single source of the [ATTACHED DOCUMENT CONTENT] marker; called from
    loop.metta when building $send so every provider path gets the document."""
    from src.media_handler import get_pending_context
    context = get_pending_context()
    if not context:
        return ""
    return "\n\n[ATTACHED DOCUMENT CONTENT]\n" + context


def pending_media_count() -> int:
    """Number of pending out-of-band media blocks (0 if none). Called from
    loop.metta to decide whether an OpenAI turn must go through callProvider
    (vision-capable) instead of useGPT (text-only)."""
    from src.media_handler import get_pending_media
    media = get_pending_media()
    return len(media) if media else 0


def callProvider(provider_name: str, content: str, max_tokens: int = 6000) -> str:
    """Generic dispatcher for MeTTa. Document context is injected upstream in
    loop.metta ($send); this only transports the prompt text + media."""
    from src.media_handler import get_pending_media
    provider = _get_provider(provider_name)
    if not provider or not provider.is_available:
        raise RuntimeError(f"Provider '{provider_name}' not available")
    media = get_pending_media()
    return provider.chat(content=content, max_tokens=max_tokens, media=media)



def _chatAsiOne(client, model, content, max_tokens=6000, **kwargs):
    spl = content.split(":-:-:-:")
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": spl[0]},
                      {"role": "user", "content": spl[1]}],
            max_tokens=max_tokens,
            extra_body={
                "enable_thinking": True,
                "thinking_budget": 6000 
            },
            **kwargs
        )
        return _clean(resp.choices[0].message.content)
    except Exception as e:
        print(f"[lib_llm_ext._chat] Exception while communicating with LLM: {e}")
        return ""

def useAsi1(content):
    resp = _chatAsiOne(
        client=ASIONE_CLIENT,
        model="asi1-ultra", # "asi1-ultra"
        content=content
    )
    resp = resp.replace("</arg_value>", " ").replace("</tool_call>", " ").replace("<arg_value>", " ").replace("<tool_call>", " ")
    return resp

_embedding_model = None

def initLocalEmbedding():
    model_name="intfloat/e5-large-v2"
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer(model_name)
    return _embedding_model

def useLocalEmbedding(atom):
    global _embedding_model
    if _embedding_model is None:
        raise RuntimeError("Call initLocalEmbedding() first.")
    return _embedding_model.encode(
        atom,
        normalize_embeddings=True
    ).tolist()
