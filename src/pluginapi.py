"""
OmegaClaw API which should be used to implement extensions.
"""

_commChannelRegistry = {}
_llmProviderRegistry = {}

class CommChannel:
    """Communication channel implementation"""

    def config(self, config: dict) -> None:
        """Configure communication channel. Receives the subset of the command
        line parameters which are <key>=<value> pairs"""
        raise NotImplementedError()

    def receive(self) -> str:
        """Receive message from the communication channel"""
        raise NotImplementedError()

    def send(self, message: str) -> None:
        """Send message via the communication channel"""
        raise NotImplementedError()

def registerCommChannel(id: str, channel: CommChannel) -> None:
    """Register communication channel in the registry"""
    global _commChannelRegistry
    print(f"registerCommChannel: registering communication channel {id}")
    _commChannelRegistry[id] = channel


class LLMProvider:
    """LLM provider implementation"""

    def config(self, config: dict) -> None:
        """Configure LLM provider. Receives the subset of the command line
        parameters or configuration file keys which are started by "<id>_"
        prefix"""
        raise NotImplementedError()

    def chat(self, prompt: str, max_tokens: int = 6000, reasoning_mode: str = "medium") -> str:
        """Chat with LLM provider"""
        raise NotImplementedError()

def registerLLMProvider(id: str, provider: LLMProvider) -> None:
    """Register LLM provider in the registry"""
    global _llmProviderRegistry
    print(f"registerLLMProvider: registering LLM provider {id}")
    _llmProviderRegistry[id] = provider
