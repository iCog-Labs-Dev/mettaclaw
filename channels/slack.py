import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from channels.base import BaseChannel

_AUTO_BIND_REFRESH_INTERVAL = 300

class _RateLimitError(Exception):
    def __init__(self, retry_after):
        super().__init__(f"Rate limited (retry after {retry_after}s)")
        self.retry_after = retry_after

class SlackChannel(BaseChannel):
    def __init__(self, bot_token, channel_id="", poll_interval=60):
        super().__init__()
        self._bot_token = str(bot_token).strip()
        if not self._bot_token:
            raise ValueError("SL_BOT_TOKEN is required")
        self._channel_id = str(channel_id).strip()
        self._poll_interval = max(1, int(poll_interval))
        self._bot_user_id = ""
        self._user_cache: dict = {}
        self._channel_offsets: dict = {}
        self._channel_name_cache: dict = {}
        self._auto_bind_channels: list = []
        self._auto_bind_index = 0
        self._auto_bind_last_refresh = 0.0
        self._rate_limit_until = 0.0

    def _is_allowed_message(self, sender_id: str, msg: str, channel_id: str = "") -> str:
        candidate = self._parse_auth_candidate(msg)
        with self._auth_lock:
            if self._channel_id and channel_id != self._channel_id:
                return "ignore"
            if not self._auth_secret:
                if not self._channel_id:
                    label = self._channel_name_cache.get(channel_id, channel_id)
                    print(f"[SLACK] Auto-bound to channel {label}")
                    self._channel_id = channel_id
                return "allow"
            if candidate == self._auth_secret:
                if self._authenticated_id is None:
                    self._authenticated_id = sender_id
                    self._channel_id = channel_id
                    return "auth_bound"
                return "ignore"
            if self._authenticated_id is None:
                return "ignore"
            return "allow" if sender_id == self._authenticated_id else "ignore"

    def _api_call(self, method, params=None, timeout=30):
        params = params or {}
        body = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(
            f"https://slack.com/api/{method}", data=body,
            headers={"Authorization": f"Bearer {self._bot_token}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with self._auth_lock:
            wait = self._rate_limit_until - time.time()
        if wait > 0:
            time.sleep(wait)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
                headers = resp.headers
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                retry = max(1, int(exc.headers.get("Retry-After", 60)))
                with self._auth_lock:
                    self._rate_limit_until = time.time() + retry
                raise _RateLimitError(retry) from exc
            raise
        if not payload.get("ok"):
            err = payload.get("error", f"{method} failed")
            if err == "ratelimited":
                retry = max(1, int(headers.get("Retry-After", 60)))
                with self._auth_lock:
                    self._rate_limit_until = time.time() + retry
                raise _RateLimitError(retry)
            raise RuntimeError(err)
        return payload

    def _get_display_name(self, user_id):
        with self._auth_lock:
            cached = self._user_cache.get(user_id)
        if cached:
            return cached
        name = user_id
        try:
            payload = self._api_call("users.info", {"user": user_id}, timeout=15)
            profile = (payload.get("user") or {}).get("profile") or {}
            name = (profile.get("display_name") or profile.get("real_name") or
                    (payload["user"].get("name")) or user_id).strip()
        except Exception as exc:
            print(f"[SLACK] Could not resolve user {user_id}: {exc}")
        with self._auth_lock:
            self._user_cache[user_id] = name
        return name

    def _cache_channel(self, channel):
        cid = str(channel.get("id", "")).strip()
        name = str(channel.get("name", "")).strip()
        if cid:
            self._channel_name_cache[cid] = f"#{name}" if name else cid

    def _list_joined_channels(self):
        channels, cursor = [], ""
        while True:
            params = {"types": "public_channel,private_channel",
                      "exclude_archived": "true", "limit": 200}
            if cursor:
                params["cursor"] = cursor
            payload = self._api_call("conversations.list", params=params, timeout=20)
            for ch in payload.get("channels") or []:
                if ch.get("is_member"):
                    cid = str(ch.get("id", "")).strip()
                    if cid:
                        self._cache_channel(ch)
                        channels.append(cid)
            cursor = str((payload.get("response_metadata") or {}).get("next_cursor", "")).strip()
            if not cursor:
                break
        return channels

    def _init_cursor(self, channel_id):
        try:
            payload = self._api_call("conversations.history",
                                     {"channel": channel_id, "limit": 1}, timeout=15)
            msgs = payload.get("messages") or []
            ts = str(msgs[0].get("ts", "")).strip() if msgs else ""
            self._channel_offsets[channel_id] = ts
        except Exception as exc:
            print(f"[SLACK] Could not initialize cursor for {channel_id}: {exc}")

    def _refresh_auto_bind(self, force=False):
        now = time.time()
        if (not force) and self._auto_bind_channels and \
                (now - self._auto_bind_last_refresh) < _AUTO_BIND_REFRESH_INTERVAL:
            return self._auto_bind_channels
        self._auto_bind_channels = self._list_joined_channels()
        self._auto_bind_last_refresh = now
        if self._auto_bind_index >= len(self._auto_bind_channels):
            self._auto_bind_index = 0
        return self._auto_bind_channels

    def _next_auto_bind_channel(self):
        if not self._auto_bind_channels:
            return ""
        cid = self._auto_bind_channels[self._auto_bind_index]
        self._auto_bind_index = (self._auto_bind_index + 1) % len(self._auto_bind_channels)
        return cid

    def _poll_channel(self, channel_id):
        oldest = self._channel_offsets.get(channel_id, "")
        params = {"channel": channel_id, "limit": 15}
        if oldest:
            params["oldest"] = oldest
            params["inclusive"] = "false"
        payload = self._api_call("conversations.history", params=params, timeout=30)
        messages = sorted(payload.get("messages") or [],
                          key=lambda m: float(m.get("ts", 0.0)))
        max_ts = oldest
        for msg in messages:
            ts = str(msg.get("ts", "")).strip()
            if ts:
                max_ts = ts
            if msg.get("subtype"):
                continue
            text = str(msg.get("text", "")).strip()
            user_id = str(msg.get("user", "")).strip()
            if not text or not user_id or user_id == self._bot_user_id:
                continue
            state = self._is_allowed_message(user_id, text, channel_id)
            name = self._get_display_name(user_id)
            if state == "allow":
                self._set_last(f"{name}: {text}")
            elif state == "auth_bound":
                self.send_message(f"Authentication successful for {name}.")
        if max_ts != oldest:
            self._channel_offsets[channel_id] = max_ts

    def _run_loop(self) -> None:
        print("[SLACK] Polling started")
        while self._running:
            try:
                with self._auth_lock:
                    bound = self._channel_id
                if bound:
                    if bound not in self._channel_offsets:
                        self._init_cursor(bound)
                    else:
                        self._poll_channel(bound)
                else:
                    channels = self._refresh_auto_bind() or self._refresh_auto_bind(force=True)
                    cid = self._next_auto_bind_channel()
                    if cid:
                        if cid not in self._channel_offsets:
                            self._init_cursor(cid)
                        else:
                            self._poll_channel(cid)
                self._connected = True
            except _RateLimitError as exc:
                self._connected = False
                print(f"[SLACK] Rate limited. Backing off {exc.retry_after}s.")
            except Exception as exc:
                self._connected = False
                print(f"[SLACK] Poll error: {exc}")
            time.sleep(max(1, self._poll_interval))
        self._connected = False
        print("[SLACK] Polling stopped")

    def send_message(self, text: str) -> None:
        text = str(text).replace("\\n", "\n").replace("\r", "")
        if not text:
            return
        with self._auth_lock:
            target = self._channel_id
        if not target:
            return
        for i in range(0, len(text), 3900):
            chunk = text[i:i + 3900]
            try:
                self._api_call("chat.postMessage", {"channel": target, "text": chunk}, timeout=15)
            except Exception as exc:
                print(f"[SLACK] Send failed: {exc}")
                return

    def start(self, auth_secret=None) -> threading.Thread:
        self._set_auth_secret(auth_secret)
        payload = self._api_call("auth.test", timeout=15)
        self._bot_user_id = str(payload.get("user_id", "")).strip()
        if self._channel_id:
            info = self._api_call("conversations.info",
                                  {"channel": self._channel_id}, timeout=15)
            self._cache_channel(info.get("channel") or {})
            self._init_cursor(self._channel_id)
            print(f"[SLACK] Channel ready: "
                  f"{self._channel_name_cache.get(self._channel_id, self._channel_id)}")
        else:
            print("[SLACK] Starting in auto-bind mode.")
            self._refresh_auto_bind(force=True)
        print(f"[SLACK] Starting adapter with channel target: {self._channel_id or 'auto-bind'}")
        return super().start(auth_secret=None)  # secret already set above

_instance: SlackChannel | None = None

def start_slack(bot_token, channel_id="", poll_interval=60, auth_secret=None):
    global _instance
    _instance = SlackChannel(bot_token, channel_id, poll_interval)
    return _instance.start(auth_secret)

def stop_slack():
    if _instance:
        _instance.stop()

def getLastMessage() -> str:
    return _instance.getLastMessage() if _instance else ""


def send_message(text: str) -> None:
    if _instance:
        _instance.send_message(text)
