import lib_llm_ext as llm
import pluginapi as plugin

class OpenAIAPI(plugin.LLMProvider):

    def __init__(self, name):
        super().__init__()
        self._name = name

    def api_token_var(self, config):
        return config.get("api_token_var")

    def model(self, config):
        return config.get("model")

    def base_url(self, config):
        return config.get("base_url")

    def config(self, config: dict) -> None:
        self.delegate = llm.AIProvider(self._name, self.api_token_var(config),
                                       self.model(config), self.base_url(config))

    def chat(self, prompt: str, max_tokens: int = 6000, reasoning_mode: str = "medium") -> str:
        return self.delegate.chat(prompt, max_tokens, reasoning_mode)

class OllamaLocal(OpenAIAPI):

    def __init__(self):
        super().__init__("Ollama-local")

    def api_token_var(self, config):
        return "OLLAMA_API_KEY"

    def model(self, config):
        return config.get("model", "qwen3.5:9b")

    def base_url(self, config):
        return config.get("base_url", "http://localhost:11434/v1")


class OpenAIAPIPreconfigured(OpenAIAPI):

    def __init__(self, name, api_token_var, default_model, base_url):
        super().__init__(name)
        self._api_token_var = api_token_var
        self._default_model = default_model
        self._base_url = base_url

    def api_token_var(self, config):
        return self._api_token_var

    def model(self, config):
        return config.get("model", self._default_model)

    def base_url(self, config):
        return self._base_url

def loadOmegaClawPlugin():
    plugin.registerLLMProvider("OpenAIAPI", OpenAIAPI("OpenAIAPI"))
    # FIXME: this is not needed in fact it can be replaced by OpenAIAPI
    plugin.registerLLMProvider("Ollama-local", OllamaLocal())
    plugin.registerLLMProvider("ASICloud", OpenAIAPIPreconfigured("ASICloud", "ASI_API_KEY", "minimax/minimax-m3", "https://inference.asicloud.cudos.org/v1"))
    plugin.registerLLMProvider("Anthropic", OpenAIAPIPreconfigured("Anthropic", "ANTHROPIC_API_KEY", "claude-opus-4-8", "https://api.anthropic.com/v1/"))
