# FIXME: rename this module to be unique for instance omegaclaw_plugin_api
from collections.abc import Callable

_commChannelRegistry = {}
_llmProviderRegistry = {}

class CommChannel:

    def config(self, config: dict) -> None:
        raise NotImplementedError()

    def receive(self) -> str:
        raise NotImplementedError()

    def send(self, message: str) -> None:
        raise NotImplementedError()

def registerCommChannel(id: str, channel: CommChannel) -> None:
    global _commChannelRegistry
    print(f"registerCommChannel: registering communication channel {id}")
    _commChannelRegistry[id] = channel


class LLMProvider:

    def config(self, config: dict) -> None:
        raise NotImplementedError()

    def chat(self, prompt: str, max_tokens: int = 6000, reasoning_mode: str = "medium") -> str:
        raise NotImplementedError()

def registerLlmProvider(id: str, provider: LLMProvider) -> None:
    global _llmProviderRegistry
    print(f"registerLlmProvider: registering LLM provider {id}")
    _llmProviderRegistry[id] = provider
