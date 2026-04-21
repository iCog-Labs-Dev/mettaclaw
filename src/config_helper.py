import yaml
import os
import logging
import re
import openai
import asyncio

_config_cache = None
_config_mtime = 0
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "memory", "telegram_profile.yaml")
_openai_api_key = os.environ.get("OPENAI_API_KEY")
openai_client = openai.AsyncOpenAI(api_key=_openai_api_key) if _openai_api_key else None

def _load_config():
    """Loads and caches the telegram profile YAML configuration."""
    global _config_cache, _config_mtime

    if _config_cache is not None:
        return _config_cache
    
    if not os.path.exists(CONFIG_PATH):
        raise FileNotFoundError(f"Critical Error: Telegram profile not found at {CONFIG_PATH}")
    
    try:
        with open(CONFIG_PATH, "r") as f:
            _config_cache = yaml.safe_load(f)
    except Exception as e:
        logging.error(f"Error loading {CONFIG_PATH}: {e}")
        _config_cache = {}
        
    return _config_cache

def is_tool_disabled(tool_name):
    """Checks if a specified tool is disabled in the configuration."""
    config = _load_config()
    return config.get("disabled_tools", {}).get(tool_name, False)

def get_blocked_ethics_categories():
    """Retrieves the list of blocked ethics categories."""
    config = _load_config()
    categories = config.get("ethics_pass", {}).get("blocked_categories", [])
    return categories

def get_forbidden_memory_categories():
    """Retrieves the list of forbidden categories for memory storage."""
    config = _load_config()
    return config.get("internal_learning", {}).get("durable_memory", {}).get("categories_forbidden", [])

async def use_model(text, categories):
    """Uses an LLM to classify if text violates the provided categories."""
    config = _load_config()
    model_config = config.get("classification_model", {})
    model_name = model_config.get("name", "gpt-4o-mini")
    max_tokens = model_config.get("max_tokens", 10)
    temperature = model_config.get("temperature", 0.0)

    try: 
        prompt = (
            f"Does the following expression fall into any of these strictly forbidden categories?\n\n"
            f"Forbidden Categories: {', '.join(categories)}\n\n"
            f"Expression: '{text}'\n\n"
            f"Answer ONLY 'YES' if it violates a category, or 'NO' if it is safe."
        )
        client = openai.AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
        response = await client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature
        )
        answer = response.choices[0].message.content.strip().upper()
        return "YES" in answer
    
    except Exception as e:
        logging.error(f"OpenAI classification error: {e}")
        return False

async def _llm_classify(text, categories, memCheck=False):
    """Classifies text using OpenAI's Moderation API or falls back to standard LLM classification."""
    if not categories or not text.strip():
        return False
    
    if memCheck:
        return await use_model(text, categories)
            
    try:
        if openai_client is None:
            return await use_model(text, categories)
        response = await openai_client.moderations.create(input=text)
        return response.results[0].flagged
        
    except Exception as e:
        logging.error(f"OpenAI moderation error: {e}")
        logging.info(f"Opting to model usage for classification...")
        return await use_model(text, categories)

async def is_category_blocked(text):
    """Checks if the text violates any blocked ethics categories."""
    config = _load_config()
    blocked = config.get("ethics_pass", {}).get("blocked_categories", [])
    return await _llm_classify(text, blocked)


async def is_memory_forbidden(text):
    """Checks if the text contains topics forbidden from long-term memory."""
    config = _load_config()
    forbidden = config.get("internal_learning", {}).get("durable_memory", {}).get("categories_forbidden", [])
    text = text.lower()
    return await _llm_classify(text, forbidden)
    
def get_spam_protection_config():
    """Retrieves spam protection thresholds from the configuration."""
    config = _load_config()
    spam_config = config.get("spam_protection", {})
    return {
        "time_window": spam_config.get("time_window", 10),
        "message_limit": spam_config.get("message_limit", 5),
        "cooldown_duration": spam_config.get("cooldown_duration", 120),
        "admin_alert_threshold": spam_config.get("admin_alert_threshold", 3)
    }


def get_telegram_tool_policy():
    """Retrieves Telegram-specific tool policy flags from configuration."""
    config = _load_config()
    telegram_cfg = config.get("telegram", {})
    return telegram_cfg.get("tool_policy", {})


def is_tool_blocked_in_telegram(tool_name):
    """Checks whether a tool is blocked when running in Telegram mode."""
    key_map = {
        "shell": "disable_shell",
        "write-file": "disable_write_file",
        "append-file": "disable_append_file",
        "metta": "disable_metta_eval",
    }
    policy = get_telegram_tool_policy()
    policy_key = key_map.get(str(tool_name), "")
    if not policy_key:
        return False
    return bool(policy.get(policy_key, False))
