from collections import deque
import shlex
import re
import os
from datetime import datetime

TS_RE = re.compile(r'^\("(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"')

def extract_timestamp(line):
    m = TS_RE.search(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

def around_time(needle_time_str, k):
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    primary = os.path.join(project_root, "memory", "history.metta")
    legacy = os.path.join(project_root, "repos", "OmegaClaw-Core", "memory", "history.metta")
    filename = primary if os.path.exists(primary) else legacy
    target = datetime.strptime(needle_time_str, "%Y-%m-%d %H:%M:%S")
    best_lineno = None
    best_line = None
    best_diff = None
    buffer = []
    best_idx = None
    with open(filename, "r", encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            buffer.append((lineno, line))
            ts = extract_timestamp(line)
            if ts is None:
                continue
            diff = abs((ts - target).total_seconds())
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_lineno = lineno
                best_line = line
                best_idx = len(buffer) - 1
    if best_lineno is None:
        return
    start = max(0, best_idx - k)
    end = min(len(buffer), best_idx + k + 1)
    ret = ""
    for lineno, line in buffer[start:end]:
        ret += f"{lineno}:{line}"
    return ret

RAW_ARG_COMMANDS = {"metta"}
TWO_ARG_COMMANDS = {"write-file", "append-file", "append-file-raw"}


def _metta_quote(value):
    value = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{value}"'


def _normalize_command_line(line):
    line = line.strip()
    if line.startswith("(") and line.endswith(")"):
        line = line[1:-1].strip()
    return line


def _parse_single_arg(rest):
    rest = rest.strip()
    if not rest:
        return None
    try:
        tokens = shlex.split(rest, posix=True)
    except ValueError:
        tokens = None
    if tokens is None:
        return rest
    if len(tokens) == 1:
        return tokens[0]
    return rest


def _parse_two_args(rest):
    rest = rest.strip()
    if not rest:
        return None, None
    try:
        tokens = shlex.split(rest, posix=True)
    except ValueError:
        return rest, ""
    if not tokens:
        return None, None
    if len(tokens) == 1:
        return tokens[0], ""
    return tokens[0], " ".join(tokens[1:])


def _format_line_as_command(line):
    normalized = _normalize_command_line(line)
    if not normalized:
        return None

    parts = normalized.split(maxsplit=1)
    cmd = parts[0]
    rest = parts[1] if len(parts) > 1 else ""

    if not rest:
        return f"({cmd})"

    if cmd in RAW_ARG_COMMANDS:
        return f"({cmd} {rest})"

    if cmd in TWO_ARG_COMMANDS:
        arg1, arg2 = _parse_two_args(rest)
        if arg1 is None:
            return f"({cmd})"
        return f"({cmd} {_metta_quote(arg1)} {_metta_quote(arg2 or '')})"

    arg = _parse_single_arg(rest)
    if arg is None:
        return f"({cmd})"
    return f"({cmd} {_metta_quote(arg)})"


def balance_parentheses(s):
    s = (s.replace("_quote_", '"')
           .replace("_apostrophe_", "'")
           .replace("_newline_", "\n"))
    sexprs = []
    for line in s.splitlines():
        line = line.strip()
        if not line:
            continue
        command_expr = _format_line_as_command(line)
        if command_expr:
            sexprs.append(command_expr)
    ret = " ".join(sexprs)
    return "(" + ret + ")"

def normalize_string(x):
    try:
        if isinstance(x, bytes):
            return x.decode("utf-8", errors="ignore")
        return str(x).encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")
    except Exception:
        return str(x)
