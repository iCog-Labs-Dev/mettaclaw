import json
import time
import threading
import requests
import websocket

from channels.base import BaseChannel

class MattermostChannel(BaseChannel):
    def __init__(self, url, channel_id, bot_token):
        super().__init__()
        self._url = url.rstrip("/")
        self._channel_id = channel_id
        self._bot_token = bot_token
        self._headers = {"Authorization": f"Bearer {bot_token}"}
        self._bot_user_id = ""

    def _get(self, path):
        return requests.get(f"{self._url}/api/v4{path}", headers=self._headers).json()

    def _get_bot_user_id(self):
        return self._get("/users/me")["id"]

    def _get_display_name(self, user_id):
        u = self._get(f"/users/{user_id}")
        if u.get("first_name") or u.get("last_name"):
            return f"{u.get('first_name','')} {u.get('last_name','')}".strip()
        return u["username"]

    def _run_loop(self) -> None:
        ws_url = self._url.replace("https", "wss") + "/api/v4/websocket"
        ws = websocket.WebSocket()
        ws.connect(ws_url, header=[f"Authorization: Bearer {self._bot_token}"])
        self._bot_user_id = self._get_bot_user_id()
        self._connected = True
        last_ping = time.time()
        print("[MATTERMOST] Connected")

        while self._running:
            try:
                if time.time() - last_ping > 25:
                    ws.ping()
                    last_ping = time.time()
                ws.settimeout(1)
                event = json.loads(ws.recv())
                if event.get("event") == "posted":
                    post = json.loads(event["data"]["post"])
                    if post["channel_id"] == self._channel_id \
                            and post["user_id"] != self._bot_user_id:
                        user_id = post["user_id"]
                        msg = post.get("message", "")
                        state = self._is_allowed_message(user_id, msg)
                        if state == "allow":
                            name = self._get_display_name(user_id)
                            self._set_last(f"{name}: {msg}")
                        elif state == "auth_bound":
                            name = self._get_display_name(user_id)
                            self.send_message(f"Authentication successful for {name}.")
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break

        ws.close()
        self._connected = False
        print("[MATTERMOST] Disconnected")

    def send_message(self, text: str) -> None:
        text = text.replace("\\n", "\n")
        if not self._connected:
            return
        requests.post(f"{self._url}/api/v4/posts", headers=self._headers,
                      json={"channel_id": self._channel_id, "message": text})

_instance: MattermostChannel | None = None

def start_mattermost(url, channel_id, bot_token, auth_secret=None):
    global _instance
    _instance = MattermostChannel(url, channel_id, bot_token)
    return _instance.start(auth_secret)

def stop_mattermost():
    if _instance:
        _instance.stop()

def getLastMessage() -> str:
    return _instance.getLastMessage() if _instance else ""


def send_message(text: str) -> None:
    if _instance:
        _instance.send_message(text)
