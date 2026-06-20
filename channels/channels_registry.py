import importlib

_REGISTRY = {
    "irc": (
        "channels.irc", "start_irc",
        lambda token, channel_id, poll_interval, server_url: (
            channel_id or "##omegaclaw",
            server_url or "irc.quakenet.org",
            6667,
            "omegaclaw",
        ),
    ),
    "telegram": (
        "channels.telegram", "start_telegram",
        lambda token, channel_id, poll_interval, server_url: (
            token,
            channel_id,
            poll_interval,
        ),
    ),
    "slack": (
        "channels.slack", "start_slack",
        lambda token, channel_id, poll_interval, server_url: (
            token,
            channel_id,
            poll_interval,
        ),
    ),
    "mattermost": (
        "channels.mattermost", "start_mattermost",
        lambda token, channel_id, poll_interval, server_url: (
            server_url or "https://chat.singularitynet.io",
            channel_id or "8fjrmabjx7gupy7e5kjznpt5qh",
            token,
        ),
    ),
    "mock": (
        "channels.mock", "start_mock",
        lambda token, channel_id, poll_interval, server_url: (),
    ),
}

_active_module = None


def start(channel_name: str, token="", channel_id="", poll_interval=20, server_url=""):
    global _active_module
    channel_name = str(channel_name)
    entry = _REGISTRY.get(channel_name)
    if not entry:
        raise ValueError(f"Unknown channel: {channel_name}")
    module_path, start_fn, args_fn = entry
    args = args_fn(str(token), str(channel_id), int(poll_interval), str(server_url))
    mod = importlib.import_module(module_path)
    _active_module = mod
    return getattr(mod, start_fn)(*args)


def getLastMessage() -> str:
    return _active_module.getLastMessage() if _active_module else ""


def send_message(text: str) -> None:
    if _active_module:
        _active_module.send_message(text)
