import abc
import threading

import auth


class BaseChannel(abc.ABC):
    def __init__(self):
        self._last_message = ""
        self._msg_lock = threading.Lock()

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

    @staticmethod
    def _parse_auth_candidate(msg: str) -> str:
        text = msg.strip()
        lower = text.lower()
        if lower.startswith("auth "):
            return text[5:].strip()
        if lower.startswith("/auth "):
            return text[6:].strip()
        return text

    @staticmethod
    def _is_auth_command(msg: str) -> bool:
        lower = msg.strip().lower()
        return lower.startswith("auth ") or lower.startswith("/auth ")

    def _is_allowed_message(self, sender_id: str, msg: str) -> str:
        with self._auth_lock:
            if not auth.is_auth_enabled():
                return "allow"
            if self._authenticated_id is not None:
                return "allow" if sender_id == self._authenticated_id else "ignore"
            if not self._is_auth_command(msg):
                return "ignore"
            candidate = self._parse_auth_candidate(msg)
            if auth.verify_token(candidate):
                self._authenticated_id = sender_id
                return "auth_bound"
            return "ignore"

    def start(self) -> threading.Thread:
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
