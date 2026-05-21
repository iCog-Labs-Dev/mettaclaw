import os
import logging
from pathlib import Path
from infisical_client import (
    InfisicalClient, ClientSettings, AuthenticationOptions,
    UniversalAuthMethod, ListSecretsOptions
)

logger = logging.getLogger(__name__)

# Load .env from OmegaClaw-Core root if present
_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            key, _, value = line.partition('=')
            os.environ.setdefault(key.strip(), value.strip())

INFISICAL_CLIENT_ID     = os.environ.get("INFISICAL_CLIENT_ID")
INFISICAL_CLIENT_SECRET = os.environ.get("INFISICAL_CLIENT_SECRET")
INFISICAL_PROJECT_ID    = os.environ.get("INFISICAL_PROJECT_ID")
INFISICAL_ENVIRONMENT   = os.environ.get("INFISICAL_ENVIRONMENT", "dev")

def get_env(key, default="None"):
    return os.environ.get(key, default)

def load_secrets():
    """Fetch all secrets from Infisical and inject into os.environ."""
    if not all([INFISICAL_CLIENT_ID, INFISICAL_CLIENT_SECRET, INFISICAL_PROJECT_ID]):
        logger.info("Infisical not configured, skipping secret loading.")
        return

    try:
        client = InfisicalClient(ClientSettings(
            auth=AuthenticationOptions(
                universal_auth=UniversalAuthMethod(
                    client_id=INFISICAL_CLIENT_ID,
                    client_secret=INFISICAL_CLIENT_SECRET
                )
            )
        ))

        secrets = client.listSecrets(options=ListSecretsOptions(
            environment=INFISICAL_ENVIRONMENT,
            project_id=INFISICAL_PROJECT_ID
        ))

        loaded = []
        for secret in secrets:
            os.environ[secret.secret_key] = secret.secret_value
            loaded.append(secret.secret_key)

        # Inject config values into sys.argv so MeTTa configure system picks them up
        import sys
        config_keys = ["commchannel", "provider", "LLM", "embeddingprovider", "BOT_TOKEN", "CHAT_ID"]
        existing_args = " ".join(sys.argv)
        for key in config_keys:
            val = os.environ.get(key.upper(), os.environ.get(key))
            if val and f"{key}=" not in existing_args:
                sys.argv.append(f"{key}={val}")

        logger.info(f"Loaded {len(loaded)} secrets from Infisical: {loaded}")

    except Exception as e:
        logger.error(f"Failed to load secrets from Infisical: {e}")
