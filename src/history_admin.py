"""Admin inspection and management of the agent's conversation history.

The agent appends every human turn and its own response to ``memory/history.metta``
(see ``src/memory.metta`` -> ``addToHistory``). This module gives Telegram admins a
safe, button-driven way to inspect, page through, delete, and purge that file.

Design note
-----------
All logic here is **pure and stdlib-only** (no aiogram / network imports), so it can
be unit-tested in isolation and reused by other channels. The Telegram layer in
``channels/tg_channel.py`` only renders the view-models produced here.

This keeps ``tg_channel.py`` thin: the file/parse logic lives in one testable module
rather than being inlined into the channel.
"""

import hashlib
import os
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_HISTORY_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "memory", "history.metta")
)

# Telegram hard-caps a message at 4096 chars; stay comfortably under it.
TELEGRAM_SAFE_LEN = 3900
DEFAULT_PAGE_SIZE = 5
PREVIEW_WIDTH = 90

# Each top-level history entry starts at line-start with a quoted timestamp,
# e.g.  ("2026-06-13 12:00:00" ...).  This mirrors how addToHistory serializes.
_ENTRY_START_RE = re.compile(r'^\("(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"', re.MULTILINE)

# Callback-data scheme for inline buttons (kept < 64 bytes per Telegram limits).
CB_PREFIX = "hist"

# A stored entry mixes the user's message ("HUMAN_MESSAGE: ..."), the agent's
# function calls (e.g. (query ...), (pin ...)), the agent's outgoing reply
# ((send ...) -- "the response") and an optional ERROR_FEEDBACK block. Admins
# should only see relevant operational data: function calls and error messages.
# Heads whose payload is the agent's natural-language reply ("the response").
RESPONSE_HEADS = {"send"}


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class HistoryEntry:
    """One top-level entry parsed from history.metta.

    ``index`` is 1-based and stable only within a single read of the file.
    """

    index: int
    timestamp: str
    raw: str

    def preview(self, width: int = PREVIEW_WIDTH) -> str:
        flat = " ".join(self.raw.split())
        return flat if len(flat) <= width else flat[: width - 3] + "..."


# ---------------------------------------------------------------------------
# File access (read / write)
# ---------------------------------------------------------------------------

def read_entries(path: str = DEFAULT_HISTORY_PATH) -> List[HistoryEntry]:
    """Parse top-level entries from history.metta. Missing/empty file -> []."""
    if not os.path.exists(path):
        return []

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    matches = list(_ENTRY_START_RE.finditer(text))
    if not matches:
        return []

    entries: List[HistoryEntry] = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        raw = text[start:end].strip()
        if not raw:
            continue
        entries.append(
            HistoryEntry(index=len(entries) + 1, timestamp=match.group(1), raw=raw)
        )
    return entries


def _write_entries(path: str, entries: List[HistoryEntry]) -> None:
    """Atomically rewrite history.metta from ``entries`` (empty list clears it)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if entries:
            f.write("\n\n".join(e.raw.strip() for e in entries).strip() + "\n")
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def fingerprint(raw: str) -> str:
    """Short, stable content id for an entry (survives index shifts)."""
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8]


def get_entry(path: str, index: int) -> Optional[HistoryEntry]:
    entries = read_entries(path)
    if 1 <= index <= len(entries):
        return entries[index - 1]
    return None


def delete_entry(
    path: str, index: int, fp: Optional[str] = None
) -> Optional[HistoryEntry]:
    """Delete one entry, re-reading the file fresh so concurrent appends survive.

    When ``fp`` (a content fingerprint captured when the admin viewed the entry)
    is given, the entry is matched by content — preferring ``index`` but falling
    back to a content search — so an index that shifted under a concurrent write
    can never cause the wrong entry to be deleted. Returns the removed entry, or
    ``None`` if it is out of range / no longer present.
    """
    entries = read_entries(path)

    target = None
    if fp is None:
        if 1 <= index <= len(entries):
            target = index - 1
    else:
        # Prefer the entry still sitting at ``index`` if its content matches,
        # otherwise locate it by fingerprint anywhere in the (re-read) file.
        if 1 <= index <= len(entries) and fingerprint(entries[index - 1].raw) == fp:
            target = index - 1
        else:
            for i, e in enumerate(entries):
                if fingerprint(e.raw) == fp:
                    target = i
                    break

    if target is None:
        return None
    removed = entries.pop(target)
    _write_entries(path, entries)
    return removed


def purge(path: str) -> int:
    """Remove all entries. Return how many were removed."""
    count = len(read_entries(path))
    _write_entries(path, [])
    return count


def history_stats(path: str = DEFAULT_HISTORY_PATH) -> dict:
    entries = read_entries(path)
    size = os.path.getsize(path) if os.path.exists(path) else 0
    return {
        "path": path,
        "exists": os.path.exists(path),
        "entries": len(entries),
        "size_bytes": size,
        "first_timestamp": entries[0].timestamp if entries else None,
        "last_timestamp": entries[-1].timestamp if entries else None,
    }


def page_count(total: int, page_size: int = DEFAULT_PAGE_SIZE) -> int:
    if total <= 0:
        return 1
    return (total + page_size - 1) // page_size


def _clamp_page(page: int, total: int, page_size: int) -> int:
    return max(1, min(page, page_count(total, page_size)))


# ---------------------------------------------------------------------------
# Views (text)
# ---------------------------------------------------------------------------

def _truncate(text: str, limit: int = TELEGRAM_SAFE_LEN) -> str:
    return text if len(text) <= limit else text[: limit - 3] + "..."


# ---------------------------------------------------------------------------
# Relevance filter: keep only function calls + error messages.
# Excludes the user query (HUMAN_MESSAGE), the agent's (send ...) reply
# ("the response") and any free-text "thought" (anything not in an s-expr).
# ---------------------------------------------------------------------------

def _iter_balanced(text: str):
    """Yield (start, end) indices of each top-level (...) group, honouring
    double-quoted strings so parentheses inside text don't break nesting."""
    depth = 0
    in_str = False
    start = None
    for i, c in enumerate(text):
        if in_str:
            if c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "(":
            if depth == 0:
                start = i
            depth += 1
        elif c == ")" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                yield start, i + 1
                start = None


def _top_groups(text: str) -> List[str]:
    return [text[s:e] for s, e in _iter_balanced(text)]


def _head(group: str) -> Optional[str]:
    m = re.match(r'\(\s*([^\s()"]+)', group)
    return m.group(1) if m else None


def _leaf_calls(region: str) -> List[str]:
    """Return individual command s-exprs from a region, descending one level into
    a wrapping list ``((a) (b))`` but also accepting a bare ``(a)``."""
    calls: List[str] = []
    for grp in _top_groups(region):
        inner = _top_groups(grp[1:-1])
        calls.extend(inner if inner else [grp])
    return calls


def parse_relevant(raw: str) -> Tuple[List[str], List[str]]:
    """Split one entry into (function_calls, error_messages), already filtered:
    user query, the (send ...) reply and free-text thought are excluded."""
    inner = raw.strip()
    if inner.startswith("(") and inner.endswith(")"):
        inner = inner[1:-1]

    idx = inner.find("ERROR_FEEDBACK")
    cmd_region = inner if idx == -1 else inner[:idx]
    err_region = "" if idx == -1 else inner[idx + len("ERROR_FEEDBACK"):].lstrip(": \n\t")

    # The agent's command list always begins at the first "((" -- everything
    # before it is the timestamp and the (paren-free) user message.
    dbl = cmd_region.find("((")
    cmd_region = cmd_region[dbl:] if dbl != -1 else ""

    calls = [c for c in _leaf_calls(cmd_region) if _head(c) not in RESPONSE_HEADS]
    errors = _leaf_calls(err_region) if err_region.strip() else []
    return calls, errors


def relevant_view(raw: str) -> str:
    """Multi-line view of one entry: function calls + error messages only."""
    calls, errors = parse_relevant(raw)
    lines: List[str] = []
    if calls:
        lines.append("⚙️ Function calls:")
        lines.extend(f"  {c}" for c in calls)
    if errors:
        lines.append("❗ Errors:")
        lines.extend(f"  {e}" for e in errors)
    if not lines:
        lines.append("(no function calls or errors)")
    return "\n".join(lines)


def relevant_summary(raw: str, width: int = PREVIEW_WIDTH) -> str:
    """One-line summary: call heads + error count (relevant data only)."""
    calls, errors = parse_relevant(raw)
    heads = [(_head(c) or "?") for c in calls]
    parts: List[str] = []
    if heads:
        parts.append("calls: " + ", ".join(heads))
    if errors:
        parts.append(f"{len(errors)} error{'s' if len(errors) != 1 else ''}")
    summary = " · ".join(parts) if parts else "(no calls/errors)"
    return summary if len(summary) <= width else summary[: width - 3] + "..."


def format_stats(path: str = DEFAULT_HISTORY_PATH) -> str:
    s = history_stats(path)
    return "\n".join(
        [
            "\U0001F4DC History Store",
            f"Path: {s['path']}",
            f"Exists: {'yes' if s['exists'] else 'no'}",
            f"Entries: {s['entries']}",
            f"Size: {s['size_bytes']} bytes",
            f"Oldest: {s['first_timestamp'] or 'n/a'}",
            f"Latest: {s['last_timestamp'] or 'n/a'}",
        ]
    )


def format_entry(entry: HistoryEntry) -> str:
    return _truncate(
        f"\U0001F4DC History entry #{entry.index}\n"
        f"Timestamp: {entry.timestamp}\n\n{relevant_view(entry.raw)}"
    )


def format_list_page(
    path: str = DEFAULT_HISTORY_PATH,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> Tuple[str, List[HistoryEntry]]:
    """Return (text, entries_on_page). Newest entries first."""
    entries = read_entries(path)
    total = len(entries)
    if total == 0:
        return ("ℹ️ No history entries found.", [])

    page = _clamp_page(page, total, page_size)
    ordered = list(reversed(entries))  # newest first
    start = (page - 1) * page_size
    page_entries = ordered[start : start + page_size]

    header = f"\U0001F4DC History — page {page}/{page_count(total, page_size)} ({total} entries)"
    lines = [header]
    for e in page_entries:
        lines.append(f"#{e.index} · {e.timestamp}\n  {relevant_summary(e.raw)}")
    return (_truncate("\n".join(lines)), page_entries)


# ---------------------------------------------------------------------------
# Button specs (channel-agnostic)
# ---------------------------------------------------------------------------
# A keyboard is a list of rows; each row is a list of (label, callback_data).

def cb(action: str, arg: Optional[object] = None) -> str:
    return f"{CB_PREFIX}:{action}" if arg is None else f"{CB_PREFIX}:{action}:{arg}"


def parse_callback(data: str) -> Optional[Tuple[str, Optional[str]]]:
    """Parse 'hist:action[:arg]' -> (action, arg). None if not ours."""
    if not data or not data.startswith(CB_PREFIX + ":"):
        return None
    parts = data.split(":", 2)
    action = parts[1] if len(parts) > 1 else ""
    arg = parts[2] if len(parts) > 2 else None
    return (action, arg)


def menu_buttons() -> List[List[Tuple[str, str]]]:
    return [
        [("\U0001F4CA Stats", cb("stats")), ("\U0001F4DC List", cb("list", 1))],
        [("\U0001F5D1 Purge all", cb("purgeask"))],
    ]


def list_page_buttons(
    page_entries: List[HistoryEntry], page: int, total: int,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> List[List[Tuple[str, str]]]:
    rows: List[List[Tuple[str, str]]] = []
    for e in page_entries:  # one "View #n" button per entry on the page
        rows.append([(f"\U0001F50D View #{e.index}", cb("view", e.index))])

    pages = page_count(total, page_size)
    nav: List[Tuple[str, str]] = []
    if page > 1:
        nav.append(("◀️ Prev", cb("list", page - 1)))
    if page < pages:
        nav.append(("Next ▶️", cb("list", page + 1)))
    if nav:
        rows.append(nav)
    rows.append([("\U0001F519 Menu", cb("menu"))])
    return rows


def entry_buttons(index: int) -> List[List[Tuple[str, str]]]:
    return [
        [("\U0001F5D1 Delete this", cb("delask", index))],
        [("\U0001F4DC List", cb("list", 1)), ("\U0001F519 Menu", cb("menu"))],
    ]


def delete_confirm_buttons(index: int, fp: str) -> List[List[Tuple[str, str]]]:
    # callback carries both index and content fingerprint: hist:del:<index>:<fp>
    return [
        [
            (f"✅ Yes, delete #{index}", cb("del", f"{index}:{fp}")),
            ("❌ Cancel", cb("view", index)),
        ]
    ]


def purge_confirm_buttons() -> List[List[Tuple[str, str]]]:
    return [
        [
            ("✅ Yes, purge ALL", cb("purge")),
            ("❌ Cancel", cb("menu")),
        ]
    ]
