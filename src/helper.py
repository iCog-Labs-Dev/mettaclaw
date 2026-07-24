from collections import deque
import re
import hashlib
from datetime import datetime
from typing import Dict, List, Optional, Tuple

TS_RE = re.compile(r'^\("(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"')

def compact_plain(value, limit=1200):
    """
    Return a compact, single-line summary with a stable digest.
    This does not write files and does not store to LTM.
    MeTTa decides whether to pin/remember the resulting summary.
    """
    text = normalize_string(value)
    compact = re.sub(r"\s+", " ", text).strip()
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()

    if len(compact) > int(limit):
        compact = compact[: int(limit) - 3].rstrip() + "..."

    return f"sha256:{digest[:16]} chars:{len(text)} excerpt:{compact}"


def make_id(prefix="id"):
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
    return f"{prefix}-{stamp}"

def cfv2_make_id(prefix="id"):
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S%fZ")
    return f"{prefix}-{stamp}"

def cfv2_compact_plain(value, limit=1200):
    return compact_plain(value, limit)

def extract_timestamp(line):
    m = TS_RE.search(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None

def around_time(needle_time_str, k):
    needle_time_str = needle_time_str.replace(r'\"', '').replace('"', '').strip()
    filename = "repos/OmegaClaw-Core/memory/history.metta"
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

def balance_parentheses(s):
    s = s.replace("_quote_", '"').replace("_newline_", "\n")
    sexprs = []
    special_two_arg_cmds = {"write-file", "append-file"}
    for line in s.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("(-"):
            line = "(pin -" + line[2:]
        elif line.startswith("-"):
            line = "pin " + line
        # remove one outer (...) if present
        if line.startswith("(") and line.endswith(")"):
            line = line[1:-1].strip()
        parts = line.split(maxsplit=1)
        cmd = parts[0]
        rest = parts[1].strip() if len(parts) > 1 else ""
        if cmd in special_two_arg_cmds:
            if not rest:
                sexprs.append(f"({cmd})")
                continue
            # filename is first token unless already quoted
            if rest.startswith('"'):
                end = 1
                escaped = False
                while end < len(rest):
                    ch = rest[end]
                    if ch == '"' and not escaped:
                        break
                    escaped = (ch == '\\' and not escaped)
                    if ch != '\\':
                        escaped = False
                    end += 1
                if end < len(rest) and rest[end] == '"':
                    filename = rest[:end+1]
                    content = rest[end+1:].strip()
                else:
                    filename = '"' + rest[1:].replace('"', '\\"') + '"'
                    content = ""
            else:
                split_rest = rest.split(maxsplit=1)
                filename = '"' + split_rest[0].replace('"', '\\"') + '"'
                content = split_rest[1].strip() if len(split_rest) > 1 else ""
            if content:
                if content.startswith('"') and content.endswith('"'):
                    sexprs.append(f"({cmd} {filename} {content})")
                else:
                    content = content.replace('"', '\\"')
                    sexprs.append(f'({cmd} {filename} "{content}")')
            else:
                sexprs.append(f"({cmd} {filename})")
            continue
        if rest:
            if rest.startswith('"') and rest.endswith('"'):
                sexprs.append(f"({cmd} {rest})")
            else:
                rest = rest.replace('"', '\\"')
                sexprs.append(f'({cmd} "{rest}")')
        else:
            sexprs.append(f"({cmd})")
    ret = " ".join(sexprs)
    return "(" + ret + ")"

def normalize_string(x):
    try:
        if isinstance(x, bytes):
            return x.decode("utf-8", errors="ignore")
        return str(x).encode("utf-8", errors="ignore").decode("utf-8", errors="ignore")
    except Exception:
        return str(x)


# Fence tags that indicate non-Python content — skip these blocks entirely
_NON_PYTHON_FENCE_TAGS = {"text", "bash", "sh", "shell", "output", "plaintext", "console", "log", "json", "yaml", "xml", "html", "css", "markdown", "md"}

def strip_code_fences(code: str) -> str:
    """Extract and concatenate content from Python code fences only.
    Fences tagged as non-Python (```text, ```bash, etc.) are skipped.
    Discards surrounding prose. If no Python fences found, returns original stripped.
    Multiple Python fences (e.g. function def + runner) are joined with a newline.
    """
    code = code.strip()
    lines = code.splitlines()
    blocks = []
    inner = None
    skip_block = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if inner is None:
                # Opening fence — check the tag
                tag = stripped[3:].strip().lower().split()[0] if stripped[3:].strip() else ""
                skip_block = tag in _NON_PYTHON_FENCE_TAGS
                if not skip_block:
                    inner = []
            else:
                # Closing fence
                if not skip_block and inner is not None:
                    blocks.append("\n".join(inner).strip())
                inner = None
                skip_block = False
        elif inner is not None and not skip_block:
            inner.append(line)
    if not blocks:
        return code
    return "\n\n".join(b for b in blocks if b)

# ---- HyperClaw Context Frames V2 helper additions ----

def strip_metta(s: str) -> str:
    """Strip whitespace and any wrapping MeTTa repr quote pairs (handles nested layers)."""
    s = str(s).strip()
    while len(s) >= 2 and (
        (s.startswith("'") and s.endswith("'")) or
        (s.startswith('"') and s.endswith('"'))
    ):
        s = s[1:-1].strip()
    return s

def cfv2_now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _unescape_repr_id(value: str) -> str:
    value = str(value).strip()
    value = value.replace("'", "").replace('"', "")
    value = value.replace("[", "").replace("]", "")
    return value.strip()


def _balanced_exprs(text: str, head: str) -> List[str]:
    """Extract top-level balanced s-expressions whose head is `head`.

    This is a pragmatic parser for scorer/runtime helper use. It is not a full MeTTa parser,
    but it handles strings and nested parentheses well enough for Frame/FrameRef atoms.
    """
    text = str(text)
    starts = []
    token = f"({head}"
    i = 0
    while True:
        idx = text.find(token, i)
        if idx < 0:
            break
        starts.append(idx)
        i = idx + len(token)

    out = []
    for start in starts:
        depth = 0
        in_str = False
        escaped = False
        for j in range(start, len(text)):
            ch = text[j]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    out.append(text[start : j + 1])
                    break
    return out


def _field(expr: str, field_name: str) -> Optional[str]:
    """Return the raw value of a first-level-ish `(field value)` form.

    This intentionally works on the stable constructor format emitted by the MeTTa code.
    """
    pattern = f"({field_name}"
    idx = expr.find(pattern)
    if idx < 0:
        return None
    start = idx + len(pattern)
    # Skip whitespace.
    while start < len(expr) and expr[start].isspace():
        start += 1
    if start >= len(expr):
        return None
    if expr[start] == "(":
        depth = 0
        in_str = False
        escaped = False
        for j in range(start, len(expr)):
            ch = expr[j]
            if in_str:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return expr[start : j + 1]
        return None
    if expr[start] == '"':
        escaped = False
        for j in range(start + 1, len(expr)):
            ch = expr[j]
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                return expr[start : j + 1]
        return None
    # Atom/number until whitespace or close paren.
    end = start
    while end < len(expr) and not expr[end].isspace() and expr[end] != ")":
        end += 1
    return expr[start:end]

def _extract_current_frame(frame_str: str) -> str:
    """Extract the (CurrentFrame ...) block from a ContextProjection string.
    Falls back to the full string if not found.
    """
    token = "(CurrentFrame"
    idx = frame_str.find(token)
    if idx < 0:
        return frame_str
    depth = 0
    in_str = False
    escaped = False
    for j in range(idx, len(frame_str)):
        ch = frame_str[j]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return frame_str[idx : j + 1]
    return frame_str


def cfv2_refs_completed_after(index_repr, date_prefix) -> str:
    """Return completed FrameRefs whose completed-timestamp starts with or compares after date_prefix.

    date_prefix can be YYYY-MM-DD or a longer timestamp prefix. This is intentionally simple.
    """
    prefix = _unescape_repr_id(date_prefix)
    refs = []
    for ref in _balanced_exprs(str(index_repr), "FrameRef"):
        status = _unescape_repr_id(_field(ref, "status") or "")
        t = _unescape_repr_id(_field(ref, "completed-timestamp") or "")
        if status == "Completed" and t and t >= prefix:
            refs.append(ref)
    return "(" + " ".join(refs) + ")"

## TODO: Replace this using metta functions
def cfv2_select_next_frame_id(index_repr, root_mode="Fast") -> str:
    """Select highest-priority active frame matching root mode from FrameRef space.

    If multiple FrameRefs exist for a frame, the last one wins. This supports append-only refs.
    """
    mode = _unescape_repr_id(root_mode)
    latest: Dict[str, Tuple[float, str, str, str]] = {}
    for ref in _balanced_exprs(str(index_repr), "FrameRef"):
        fid = _unescape_repr_id(_field(ref, "frameID") or "")
        status = _unescape_repr_id(_field(ref, "status") or "")
        frame_mode = _unescape_repr_id(_field(ref, "frame-mode") or "")
        space = _unescape_repr_id(_field(ref, "space") or "")
        priority_raw = _unescape_repr_id(_field(ref, "priority") or "0")
        try:
            priority = float(priority_raw)
        except Exception:
            priority = 0.0
        if fid:
            latest[fid] = (priority, status, frame_mode, space)

    best_id = "NON"
    best_priority = float("-inf")
    for fid, (priority, status, frame_mode, space) in latest.items():
        if space == "Active" and status in {"Active", "Focused"} and frame_mode == mode:
            if priority > best_priority:
                best_priority = priority
                best_id = fid
    return best_id

def test_balance_parenthesis():
	assert balance_parentheses('(write-file test.txt hello world)') == '((write-file "test.txt" "hello world"))'
	assert balance_parentheses('(append-file test.txt hello world)') == '((append-file "test.txt" "hello world"))'
	assert balance_parentheses('(write-file "test.txt" hello world)') == '((write-file "test.txt" "hello world"))'
	assert balance_parentheses('(write-file "test.txt" "hello world")') == '((write-file "test.txt" "hello world"))'
	assert balance_parentheses('(write-file test.txt "hello world")') == '((write-file "test.txt" "hello world"))'
	assert balance_parentheses('(send test.xt hello world)') == '((send "test.xt hello world"))'
	assert balance_parentheses('write-file test.txt hello world') == '((write-file "test.txt" "hello world"))'
	assert balance_parentheses('append-file test.txt hello world') == '((append-file "test.txt" "hello world"))'
	assert balance_parentheses('write-file "test.txt" hello world') == '((write-file "test.txt" "hello world"))'
	assert balance_parentheses('write-file "test.txt" "hello world"') == '((write-file "test.txt" "hello world"))'
	assert balance_parentheses('write-file test.txt "hello world"') == '((write-file "test.txt" "hello world"))'
	assert balance_parentheses('send test.xt hello world') == '((send "test.xt hello world"))'

if __name__ == "__main__":
    test_balance_parenthesis()
