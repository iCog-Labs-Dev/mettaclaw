import json
import threading
import time
import urllib.parse
import urllib.request

import auth
from channels.base import BaseChannel

class TelegramChannel(BaseChannel):
    def __init__(self, bot_token, chat_id="", poll_timeout=20):
        super().__init__()
        self._bot_token = str(bot_token).strip()
        self._api_base = f"https://api.telegram.org/bot{self._bot_token}"
        self._chat_id = str(chat_id).strip()
        self._poll_timeout = max(1, int(poll_timeout))
        self._offset = None
        self._state_lock = threading.Lock()
        self._authenticated_chat_id = None

    def _is_allowed_message(self, sender_id: str, msg: str, chat_id: str = "") -> str:
        with self._auth_lock:
            if self._chat_id and chat_id != self._chat_id:
                return "ignore"
            if not auth.is_auth_enabled():
                if not self._chat_id:
                    self._chat_id = chat_id
                return "allow"
            if self._authenticated_id is not None:
                if chat_id != self._chat_id:
                    return "ignore"
                return "allow" if sender_id == self._authenticated_id else "ignore"
            if not self._is_auth_command(msg):
                return "ignore"
            candidate = self._parse_auth_candidate(msg)
            if auth.verify_token(candidate):
                self._authenticated_id = sender_id
                self._authenticated_chat_id = chat_id
                self._chat_id = chat_id
                return "auth_bound"
            return "ignore"

    def _api_call(self, method, params=None, timeout=30, use_post=False):
        params = params or {}
        url = f"{self._api_base}/{method}"
        encoded = urllib.parse.urlencode(params).encode("utf-8")
        if use_post:
            req = urllib.request.Request(url, data=encoded)
        else:
            if params:
                url = f"{url}?{urllib.parse.urlencode(params)}"
            req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
        if not payload.get("ok"):
            raise RuntimeError(payload.get("description", f"{method} failed"))
        return payload.get("result")

    def _initialize_offset(self):
        try:
            updates = self._api_call("getUpdates", {"timeout": 0}, timeout=10) or []
        except Exception as exc:
            print(f"[TELEGRAM] Could not read initial offset: {exc}")
            return
        max_update = max((u.get("update_id", -1) for u in updates), default=-1)
        if max_update >= 0:
            with self._state_lock:
                self._offset = max_update + 1

    @staticmethod
    def _display_name(user, chat):
        username = str(user.get("username", "")).strip()
        if username:
            return f"@{username}"
        full = f"{user.get('first_name','')} {user.get('last_name','')}".strip()
        if full:
            return full
        title = str(chat.get("title", "")).strip()
        return title or "telegram_user"

    def _run_loop(self) -> None:
        print("[TELEGRAM] Polling started")
        while self._running:
            try:
                params = {"timeout": self._poll_timeout}
                with self._state_lock:
                    if self._offset is not None:
                        params["offset"] = self._offset
                updates = self._api_call("getUpdates", params=params,
                                         timeout=self._poll_timeout + 10) or []
                self._connected = True
                for update in updates:
                    uid = update.get("update_id")
                    if isinstance(uid, int):
                        with self._state_lock:
                            if self._offset is None or uid + 1 > self._offset:
                                self._offset = uid + 1
                    message = update.get("message") or update.get("edited_message")
                    if not isinstance(message, dict):
                        continue
                    text = message.get("text")
                    if not text:
                        continue
                    chat = message.get("chat") or {}
                    user = message.get("from") or {}
                    chat_id = str(chat.get("id", "")).strip()
                    user_id = str(user.get("id", "")).strip()
                    if not chat_id or not user_id:
                        continue
                    state = self._is_allowed_message(user_id, text, chat_id)
                    name = self._display_name(user, chat)
                    if state == "allow":
                        self._set_last(f"{name}: {text}")
                    elif state == "auth_bound":
                        self.send_message(f"Authentication successful for {name}.")
            except Exception as exc:
                self._connected = False
                print(f"[TELEGRAM] Poll error: {exc}")
                time.sleep(2)
        self._connected = False
        print("[TELEGRAM] Polling stopped")

    def send_message(self, text: str) -> None:
        text = str(text).replace("\\n", "\n").replace("\r", "")
        if not text:
            return
        with self._auth_lock:
            target = self._chat_id
        if not self._connected or not target:
            return
        for i in range(0, len(text), 3900):
            chunk = text[i:i + 3900]
            try:
                self._api_call("sendMessage", {"chat_id": target, "text": chunk},
                               timeout=15, use_post=True)
            except Exception as exc:
                print(f"[TELEGRAM] Send failed: {exc}")
                return

    def start(self) -> threading.Thread:
        proxy = auth.get_proxy_url()
        if proxy:
            self._bot_token = "proxy"
            self._api_base = f"{proxy}/telegram"
        elif not self._bot_token:
            raise ValueError("TG_BOT_TOKEN is required")
        print(f"[TELEGRAM] Starting adapter with chat target: {self._chat_id or 'auto-bind'}")
        self._initialize_offset()
        return super().start()

_instance = None

def start_telegram(bot_token, chat_id="", poll_timeout=20):
    global _instance
    _instance = TelegramChannel(bot_token, chat_id, poll_timeout)
    return _instance.start()

def stop_telegram():
    if _instance:
        _instance.stop()

def getLastMessage() -> str:
    return _instance.getLastMessage() if _instance else ""

def send_message(text: str) -> None:
    if _instance:
        _instance.send_message(text)
