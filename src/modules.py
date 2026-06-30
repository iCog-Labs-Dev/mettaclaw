"""
Module registry for HyperClaw attention directives.

Each module is a callable that takes (context, task) -> ModuleResult.
The registry maps module names to those callables.

Design:
- Modules are mock sub-agents for now — Python functions that behave
  like independent cognitive units.
- Each module is self-contained: it knows nothing about the directive
  that invoked it, only the context slice and task it receives.
- Adding a real sub-agent later means replacing the function body,
  not changing the registry contract.
- All LLM-backed modules use callActiveProvider so they automatically
  follow the provider the loop is configured to use.
"""

import shlex
import subprocess
import tempfile
import os
from typing import Callable, Dict

from lib_llm_ext import callActiveProvider
from src.helper import strip_code_fences, _field
from src.gates import run_gate

class ModuleResult:
    def __init__(self, output: str, success: bool, error: str = ""):
        self.output = output
        self.success = success
        self.error = error or ""

    def __repr__(self):
        status = "OK" if self.success else "ERROR"
        preview = self.output[:80].replace("\n", " ")
        return f"ModuleResult({status}: {preview})"

    def as_str(self) -> str:
        """Return the output string, prefixed with error info if failed."""
        if not self.success and self.error:
            return f"MODULE_ERROR: {self.error}\n{self.output}"
        return self.output

def _call_llm(prompt: str, max_tokens: int) -> ModuleResult:
    try:
        return ModuleResult(output=callActiveProvider(prompt, max_tokens=max_tokens), success=True)
    except Exception as e:
        return ModuleResult(output="", success=False, error=str(e))

def _strip_metta(s: str) -> str:
    """Strip whitespace and a single wrapping MeTTa repr quote pair."""
    s = str(s).strip()
    if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
        s = s[1:-1]
    return s.strip()

def _llm_primary(context: str, task: str) -> ModuleResult:
    """General generation and reasoning using the loop's active LLM provider."""
    prompt = f"TASK: {task}\n\n{context}" if task else context
    return _call_llm(prompt, max_tokens=6000)

def _code_reviewer(context: str, task: str) -> ModuleResult:
    """Code review: returns VERDICT/ISSUES/SUGGESTION for the given code."""
    prompt = (
        "You are a precise code reviewer. Your job is to review the code below "
        "and return structured feedback.\n\n"
        "FORMAT YOUR RESPONSE AS:\n"
        "VERDICT: PASS or FAIL\n"
        "ISSUES: (list each issue on a new line, or 'None' if no issues)\n"
        "SUGGESTION: (one concrete improvement if FAIL, or 'None' if PASS)\n\n"
        f"CODE TO REVIEW:\n{context}"
    )
    return _call_llm(prompt, max_tokens=1500)

def _python_executor(context: str, task: str) -> ModuleResult:
    """Runs Python code in a subprocess and returns stdout/stderr (10s timeout)."""
    code = strip_code_fences(context)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".py", delete=False, mode="w", encoding="utf-8"
        ) as f:
            f.write(code)
            tmp_path = f.name

        result = subprocess.run(
            ["python3", tmp_path],
            capture_output=True,
            text=True,
            timeout=10,
        )

        if result.returncode == 0:
            return ModuleResult(
                output=result.stdout or "(no output)",
                success=True,
            )
        else:
            return ModuleResult(
                output=result.stderr or result.stdout or "(no output)",
                success=False,
                error=f"Exit code {result.returncode}",
            )

    except subprocess.TimeoutExpired:
        return ModuleResult(
            output="",
            success=False,
            error="Execution timed out after 10 seconds",
        )
    except Exception as e:
        return ModuleResult(output="", success=False, error=str(e))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

def _researcher(context: str, task: str) -> ModuleResult:
    """Information gathering and synthesis — factual, well-structured analysis."""
    prompt = (
        "You are a focused research assistant. Analyse the provided context and "
        "produce a clear, factual, well-structured response.\n\n"
        "Guidelines:\n"
        "- Answer directly without restating or narrating the context you received\n"
        "- Be concise and precise\n"
        "- Cite reasoning, not just conclusions\n"
        "- If uncertain, say so explicitly\n\n"
        f"TASK: {task}\n\n"
        f"CONTEXT:\n{context}"
    )
    return _call_llm(prompt, max_tokens=3000)

def _critic(context: str, task: str) -> ModuleResult:
    """Identifies weaknesses and gaps — returns OVERALL/WEAKNESSES/SUGGESTIONS."""
    prompt = (
        "You are a rigorous critic. Your job is to identify weaknesses, "
        "inconsistencies, and gaps in the content below.\n\n"
        "FORMAT YOUR RESPONSE AS:\n"
        "OVERALL: (one sentence assessment)\n"
        "WEAKNESSES: (list each weakness on a new line)\n"
        "SUGGESTIONS: (one concrete suggestion per weakness)\n\n"
        f"CONTENT TO CRITIQUE:\n{context}"
    )
    return _call_llm(prompt, max_tokens=2000)

_REGISTRY: Dict[str, Callable[[str, str], ModuleResult]] = {
    "llm-primary":     _llm_primary,
    "code-reviewer":   _code_reviewer,
    "python-executor": _python_executor,
    "researcher":      _researcher,
    "critic":          _critic,
}

def invoke(name: str, context: str, task: str) -> ModuleResult:
    """Invoke a registered module by name. Returns a failed ModuleResult if the module is unknown."""
    module_fn = _REGISTRY.get(name)
    if module_fn is None:
        known = ", ".join(_REGISTRY.keys())
        return ModuleResult(
            output="",
            success=False,
            error=f"Unknown module '{name}'. Available: {known}",
        )
    return module_fn(context, task)


def register(name: str, fn: Callable[[str, str], ModuleResult]) -> None:
    """Register a new module at runtime without modifying this file."""
    _REGISTRY[name] = fn

def list_modules_formatted() -> str:
    """
    Return a human-readable string of all modules with descriptions.
    Suitable for injecting directly into a prompt.
    """
    lines = [
        f"- {name}: {(fn.__doc__ or 'No description').strip().splitlines()[0]}"
        for name, fn in _REGISTRY.items()
    ]
    return "\n".join(lines)

def parse_directive_args(arg_string: str) -> dict:
    """
    Parse a single directive argument string into its six components.
    Handles quoted criteria strings that may contain spaces, and also
    unquoted multi-word criteria (everything between gate and priority).

    Expected format:
      target task gate "criteria with spaces" priority slice
      target task gate "" priority slice
      target task gate criteria_word priority slice
      target task gate unquoted multi word criteria 1.0 slice

    Returns dict with keys: target, task, gate, criteria, priority, slice
    """
    defaults = {
        "target":   "llm-primary",
        "task":     "Generate",
        "gate":     "llm-judge",
        "criteria": "",
        "priority": "1.0",
        "slice":    "deliverables,history-summary",
    }

    try:
        parts = shlex.split(arg_string)
    except ValueError:
        parts = arg_string.split()

    if len(parts) < 3:
        result = dict(defaults)
        for i, key in enumerate(["target", "task", "gate"]):
            if i < len(parts):
                result[key] = parts[i]
        return result

    target, task, gate = parts[0], parts[1], parts[2]
    rest = parts[3:]  # everything after gate

    # Detect if criteria was properly quoted (shlex kept it as one token)
    # by checking whether the last two tokens look like priority + slice.
    # Priority is a float-like string; slice contains letters/commas.
    # Walk from the end: last token = slice, second-to-last = priority.
    if len(rest) == 0:
        criteria, priority, slice_val = "", defaults["priority"], defaults["slice"]
    elif len(rest) == 1:
        # only criteria
        criteria, priority, slice_val = rest[0], defaults["priority"], defaults["slice"]
    elif len(rest) == 2:
        # could be criteria + priority, or priority + slice
        try:
            float(rest[-1])
            criteria, priority, slice_val = rest[0], rest[1], defaults["slice"]
        except ValueError:
            try:
                float(rest[0])
                criteria, priority, slice_val = "", rest[0], rest[1]
            except ValueError:
                criteria, priority, slice_val = rest[0], defaults["priority"], rest[1]
    else:
        # 3+ tokens: last = slice (contains comma or letters, not a float),
        # second-to-last = priority (float), everything in between = criteria
        slice_val = rest[-1]
        try:
            float(rest[-2])
            priority = rest[-2]
            criteria = " ".join(rest[:-2])
        except ValueError:
            priority = defaults["priority"]
            criteria = " ".join(rest[:-1])

    return {
        "target":   target,
        "task":     task,
        "gate":     gate,
        "criteria": criteria,
        "priority": priority,
        "slice":    slice_val,
    }

def _run_directive_cycle(
    target: str, context: str, task: str,
    gate: str, criteria: str, max_attempts: int
) -> str:
    """Core directive execution cycle: invoke module, run gate, retry on failure."""
    attempts    = 0
    last_reason = ""
    output      = ""
    while attempts < max_attempts:
        retry_context = context if attempts == 0 else (
            f"{context}\n\nPREVIOUS_ATTEMPT_FAILED: {last_reason}\nPlease fix and retry."
        )
        result      = invoke(target, retry_context, task)
        output      = result.as_str()
        gate_result = run_gate(gate, output, task, criteria)
        attempts   += 1
        if gate_result.passed:
            return f"{output}|||True|||{gate_result.reason}|||{attempts}"
        last_reason = gate_result.retry_feedback()
    return f"{output}|||False|||failed after {attempts} attempts: {last_reason}|||{attempts}"

def build_slice_from_metta(fields_str: str, frame_str: str) -> str:
    """
    Bridge: extract requested fields from the frame s-expression string.
    fields_str — comma-separated field names, e.g. 'deliverables,history-summary'
    frame_str  — the full ContextProjection s-expr from contextFrameForPrompt
    """
    fields_str = _strip_metta(fields_str)
    frame_str  = _strip_metta(frame_str)
    fields     = [f.strip() for f in fields_str.split(",") if f.strip()]

    parts = []
    for field_name in fields:
        value = _field(frame_str, field_name)
        if value:
            parts.append(f"{field_name}: {value}")
    return "\n".join(parts) if parts else frame_str

def dispatch_directive_from_metta(arg_string: str, frame_str: str) -> str:
    """
    Single-string entry point called from attention.metta.
    Parses the argument string, builds the context slice, runs the
    full directive execution cycle, and returns the pipe-delimited result.
    """
    args         = parse_directive_args(_strip_metta(arg_string))
    target       = args["target"]
    task         = args["task"]
    gate         = args["gate"]
    criteria     = args["criteria"]
    slice_fields = args["slice"]

    context = build_slice_from_metta(slice_fields, frame_str)
    if not context.strip():
        context = _strip_metta(frame_str)

    return _run_directive_cycle(target, context, task, gate, criteria, max_attempts=3)

def execute_directive_from_metta(
    target: str, context: str, task: str,
    gate: str, criteria: str, max_retries: str
) -> str:
    """Bridge: run the full directive execution cycle including retry logic."""
    target   = _strip_metta(target)
    context  = _strip_metta(context)
    task     = _strip_metta(task)
    gate     = _strip_metta(gate)
    criteria = _strip_metta(criteria)
    try:
        max_ret = max(1, int(_strip_metta(max_retries)) + 1)
    except ValueError:
        max_ret = 3
    return _run_directive_cycle(target, context, task, gate, criteria, max_attempts=max_ret)


def directive_result_field(result_str: str, field: str) -> str:
    """
    Bridge: extract a field from the pipe-delimited directive result string.
    Fields: output=0, admitted=1, gate_reason=2, attempts=3
    """
    parts = str(result_str).split("|||", maxsplit=3)
    mapping = {"output": 0, "admitted": 1, "gate_reason": 2, "attempts": 3}
    idx = mapping.get(field)
    if idx is None:
        return ""
    if idx < len(parts):
        return parts[idx].strip()
    return ""
