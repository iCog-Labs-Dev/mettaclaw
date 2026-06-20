import abc
import os
import threading


class BaseChannel(abc.ABC):
    def __init__(self):
        self._last_message = ""
        self._msg_lock = threading.Lock()

        self._auth_secret = ""
        self._authenticated_id = None
        self._auth_lock = threading.Lock()

        self._running = False
        self._connected = False
        self._thread = None

    def _set_last(self, msg: str) -> None:
        with self._msg_lock:
            if self._last_message == "":
                self._last_message = msg
            else:
                self._last_message = self._last_message + " | " + msg

    def getLastMessage(self) -> str:
        with self._msg_lock:
            tmp = self._last_message
            self._last_message = ""
            return tmp

    def _set_auth_secret(self, secret=None) -> None:
        if secret is None:
            secret = os.environ.get("OMEGACLAW_AUTH_SECRET", "")
        with self._auth_lock:
            self._auth_secret = (secret or "").strip()
            self._authenticated_id = None

    @staticmethod
    def _parse_auth_candidate(msg: str) -> str:
        text = msg.strip()
        lower = text.lower()
        if lower.startswith("auth "):
            return text[5:].strip()
        if lower.startswith("/auth "):
            return text[6:].strip()
        return text

    def _is_allowed_message(self, sender_id: str, msg: str) -> str:
        candidate = self._parse_auth_candidate(msg)
        with self._auth_lock:
            if not self._auth_secret:
                return "allow"
            if candidate == self._auth_secret:
                if self._authenticated_id is None:
                    self._authenticated_id = sender_id
                    return "auth_bound"
                return "ignore"
            if self._authenticated_id is None:
                return "ignore"
            return "allow" if sender_id == self._authenticated_id else "ignore"

    def start(self, auth_secret=None) -> threading.Thread:
        self._set_auth_secret(auth_secret)
        self._running = True
        self._connected = False
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._running = False

    @abc.abstractmethod
    def _run_loop(self) -> None: ...

    @abc.abstractmethod
    def send_message(self, text: str) -> None: ...
