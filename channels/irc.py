import random
import socket
import textwrap
import threading
import time

import auth
from channels.base import BaseChannel


class IRCChannel(BaseChannel):
    def __init__(self, channel, server, port, nick):
        super().__init__()
        if not channel.startswith("#"):
            channel = f"#{channel}"
        self._channel = channel
        self._server = server
        self._port = int(port)
        self._nick = f"{nick}{random.randint(1000, 9999)}"
        self._sock = None
        self._sock_lock = threading.Lock()

    def _is_allowed_message(self, sender_id: str, msg: str) -> str:
        norm_nick = sender_id.strip().lower()
        with self._auth_lock:
            if not auth.is_auth_enabled():
                return "allow"
            if self._authenticated_id is not None:
                return "allow" if norm_nick == self._authenticated_id else "ignore"
            if not self._is_auth_command(msg):
                return "ignore"
            candidate = self._parse_auth_candidate(msg)
            if auth.verify_token(candidate):
                self._authenticated_id = norm_nick
                return "auth_bound"
            return "ignore"

    def _send_raw(self, cmd: str) -> None:
        with self._sock_lock:
            if self._sock:
                self._sock.sendall((cmd + "\r\n").encode())
        time.sleep(1)

    def send_message(self, text: str) -> None:
        segments = text.replace("\r", "").split("\\n")
        lines = []
        for seg in segments:
            lines.extend(textwrap.wrap(seg, width=400, break_long_words=True, break_on_hyphens=False))
        for chunk in lines:
            try:
                if self._connected and self._channel:
                    self._send_raw(f"PRIVMSG {self._channel} :{chunk}")
            except Exception as e:
                print(f"[IRC] send error: {e}")

    def _run_loop(self) -> None:
        print(f"[IRC] Connecting to {self._server}:{self._port} as {self._nick}")
        try:
            sock = socket.create_connection((self._server, self._port), timeout=15)
            sock.settimeout(60)
        except OSError as e:
            print(f"[IRC] Connect failed: {e}")
            return

        self._sock = sock
        self._send_raw(f"NICK {self._nick}")
        self._send_raw(f"USER {self._nick} 0 * :{self._nick}")

        buf = ""
        while self._running:
            try:
                data = sock.recv(4096).decode(errors="ignore")
                if not data:
                    break
            except socket.timeout:
                continue
            except OSError:
                break

            buf += data
            while "\r\n" in buf:
                line, buf = buf.split("\r\n", 1)
                if not line:
                    continue
                if line.startswith("PING"):
                    self._send_raw(f"PONG {line.split()[1]}")
                    continue
                parts = line.split()
                if len(parts) < 2:
                    continue
                if parts[1] == "001":
                    self._connected = True
                    print(f"[IRC] Registered. Joining {self._channel}")
                    self._send_raw(f"JOIN {self._channel}")
                elif parts[1] in {"403", "405", "471", "473", "474", "475"}:
                    print(f"[IRC] Join failed: {line}")
                elif parts[1] == "433":
                    print(f"[IRC] Nickname in use: {line}")
                elif line.startswith(":") and " PRIVMSG " in line:
                    try:
                        prefix, trailing = line[1:].split(" PRIVMSG ", 1)
                        nick = prefix.split("!", 1)[0]
                        if " :" not in trailing:
                            continue  # malformed, ignore safely
                        msg = trailing.split(" :", 1)[1]
                        state = self._is_allowed_message(nick, msg)
                        if state == "allow":
                            self._set_last(f"{nick}: {msg}")
                        elif state == "auth_bound":
                            self._send_raw(f"PRIVMSG {self._channel} :Authentication successful for {nick}.")
                    except Exception:
                        pass

        self._connected = False
        with self._sock_lock:
            self._sock = None
        sock.close()
        print("[IRC] Disconnected")


_instance = None


def start_irc(channel, server="irc.quakenet.org", port=6667, nick="omegaclaw"):
    global _instance
    _instance = IRCChannel(channel, server, port, nick)
    return _instance.start()


def stop_irc():
    if _instance:
        _instance.stop()


def getLastMessage() -> str:
    return _instance.getLastMessage() if _instance else ""


def send_message(text: str) -> None:
    if _instance:
        _instance.send_message(text)
