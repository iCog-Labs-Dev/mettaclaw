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
import resource
from typing import Callable, Dict

from lib_llm_ext import callActiveProvider
from src.helper import strip_code_fences, strip_metta, _field, _extract_current_frame
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

# Single source of truth for task-specific prompting.
# Each entry: (role_sentence, instruction_prefix)
# Used by all LLM-backed modules — modules may prepend their own persona
# before calling _task_prompt, but the task instruction always comes from here.
_TASK_TEMPLATES: dict[str, tuple[str, str]] = {
    "Generate":  ("You are a precise generator. Produce the requested output directly.",
                  "Generate the following:\n"),
    "Critique":  ("You are a rigorous critic. Identify flaws, gaps, and inconsistencies.",
                  "Critique the following:\n"),
    "Revise":    ("You are a careful revisor. Improve the content based on the feedback provided.",
                  "Revise the following, incorporating the feedback:\n"),
    "Execute":   ("You are an execution engine. Run or apply the given instructions precisely.",
                  "Execute the following:\n"),
    "Evaluate":  ("You are an objective evaluator. Assess quality, correctness, and completeness.",
                  "Evaluate the following. Format as OVERALL/SCORE/STRENGTHS/WEAKNESSES:\n"),
}

def _task_prompt(task: str, context: str, persona: str = "") -> str:
    """
    Build a prompt from the task template.
    persona: optional module-specific role sentence that replaces the default role.
    """
    role, instruction = _TASK_TEMPLATES.get(task, ("", f"TASK: {task}\n"))
    header = f"{persona or role}\n\n" if (persona or role) else ""
    return f"{header}{instruction}{context}"

_DEF_RE = __import__("re").compile(r"^\s*(class |def )", __import__("re").MULTILINE)

def _needs_test_invocation(code: str) -> bool:
    """True when code has class/def but no top-level executable call or print."""
    if not _DEF_RE.search(code):
        return False
    # If there's already a top-level call (non-indented statement after the defs)
    for line in code.splitlines():
        if line and not line[0].isspace() and not line.startswith(("class ", "def ", "#", "import ", "from ")):
            return False
    return True

def _general(context: str, task: str) -> ModuleResult:
    """General-purpose reasoning and generation using the loop's active LLM provider."""
    result = _call_llm(_task_prompt(task, context), max_tokens=6000)
    if not result.success:
        return result
    code = strip_code_fences(result.output)
    if _needs_test_invocation(code):
        test = _call_llm(
            f"Append a short test invocation to the following Python code so running it produces visible output. "
            f"Return only the complete code with the test appended, no explanation.\n\n{code}",
            max_tokens=6000,
        )
        if test.success and test.output.strip():
            result.output = test.output
    return result

def _code_reviewer(context: str, task: str) -> ModuleResult:
    """Code review: returns VERDICT/ISSUES/SUGGESTION for the given code."""
    # Output format varies by task type so the gate can check structure.
    _formats = {
        "Critique":  "VERDICT: PASS or FAIL\nISSUES: (list each issue, or 'None')\nSUGGESTION: (one concrete improvement, or 'None')",
        "Evaluate":  "VERDICT: PASS or FAIL\nSCORE: (0-10)\nSTRENGTHS: (list)\nWEAKNESSES: (list)",
        "Revise":    "REVISED_CODE:\n(full revised code here)\nCHANGES: (list of changes made)",
        "Generate":  "(produce the requested code directly, no extra commentary)",
        "Execute":   "(apply or run the instructions and report the result)",
    }
    fmt = _formats.get(task, _formats["Critique"])
    prompt = (
        f"You are a precise code reviewer.\n\n"
        f"FORMAT YOUR RESPONSE AS:\n{fmt}\n\n"
        f"CODE:\n{context}"
    )
    return _call_llm(prompt, max_tokens=1500)

def _sandbox_limits():
    """Applied in the executor child process via preexec_fn."""
    # 5s CPU time — SIGXCPU on breach
    resource.setrlimit(resource.RLIMIT_CPU,   (5,   5))
    # 256 MB virtual memory
    resource.setrlimit(resource.RLIMIT_AS,    (256 * 1024 * 1024, 256 * 1024 * 1024))
    # 1 MB max file write
    resource.setrlimit(resource.RLIMIT_FSIZE, (1 * 1024 * 1024,   1 * 1024 * 1024))
    # 32 open file descriptors (limits socket creation)
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))


def _python_executor(context: str, task: str) -> ModuleResult:
    """Runs Python code in a sandboxed subprocess (10s wall-clock, 5s CPU, 256MB RAM, 1MB writes)."""
    code = strip_code_fences(context)

    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix=".py")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(code)
        except Exception:
            os.close(fd)
            raise
        os.chmod(tmp_path, 0o600)

        result = subprocess.run(
            ["python3", tmp_path],
            capture_output=True,
            text=True,
            timeout=10,
            preexec_fn=_sandbox_limits,
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
        return ModuleResult(output="", success=False, error="Execution timed out after 10 seconds")
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
    persona = (
        "You are a focused research assistant. Produce a clear, factual, well-structured response.\n"
        "- Answer directly; do not restate the context\n"
        "- Be concise and precise; cite reasoning not just conclusions\n"
        "- If uncertain, say so explicitly"
    )
    return _call_llm(_task_prompt(task, context, persona=persona), max_tokens=3000)

def _critic(context: str, task: str) -> ModuleResult:
    """Identifies weaknesses and gaps — returns OVERALL/WEAKNESSES/SUGGESTIONS."""
    persona = (
        "You are a rigorous critic. Identify weaknesses, inconsistencies, and gaps.\n"
        "FORMAT YOUR RESPONSE AS:\n"
        "OVERALL: (one sentence assessment)\n"
        "WEAKNESSES: (list each weakness on a new line)\n"
        "SUGGESTIONS: (one concrete suggestion per weakness)"
    )
    return _call_llm(_task_prompt(task, context, persona=persona), max_tokens=2000)

_REGISTRY: Dict[str, Callable[[str, str], ModuleResult]] = {
    "general":         _general,
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
    Parse directive args: target task gate "criteria" priority slice
    Positions 0-5 are strictly positional. Criteria must be quoted if it
    contains spaces. shlex.split handles the quoting.
    """
    defaults = {
        "target":   "general",
        "task":     "Generate",
        "gate":     "nonempty",
        "criteria": "",
        "priority": "1.0",
        "slice":    "deliverables,history-summary",
    }
    try:
        parts = shlex.split(arg_string)
    except ValueError:
        parts = arg_string.split()

    keys = ["target", "task", "gate", "criteria", "priority", "slice"]
    result = dict(defaults)
    for i, key in enumerate(keys):
        if i < len(parts):
            result[key] = parts[i]
    return result

def _run_directive_cycle(
    target: str, context: str, task: str,
    gate: str, criteria: str, max_attempts: int
) -> str:
    """Core directive execution cycle: invoke module, run gate, retry on failure."""
    attempts    = 0
    last_reason = ""
    best_output = ""
    output      = ""
    while attempts < max_attempts:
        retry_context = context if attempts == 0 else (
            f"{context}\n\nPREVIOUS_ATTEMPT_FAILED: {last_reason}\nPlease fix and retry."
        )
        result      = invoke(target, retry_context, task)
        output      = result.as_str()
        if not best_output and output.strip():
            best_output = output
        gate_result = run_gate(gate, output, task, criteria)
        attempts   += 1
        if gate_result.passed:
            return f"{output}|||True|||{gate_result.reason}|||{attempts}"
        last_reason = gate_result.retry_feedback()
    failure_output = best_output or output
    return f"{failure_output}|||False|||failed after {attempts} attempts: {last_reason}|||{attempts}"

def build_slice_from_metta(fields_str: str, frame_str: str) -> str:
    """
    Bridge: extract requested fields scoped to the CurrentFrame block only.
    Returns only the matched fields. Returns empty string if none are found
    so the caller can decide on a fallback rather than flooding the module
    with the full raw frame.
    """
    fields_str    = strip_metta(fields_str)
    frame_str     = strip_metta(frame_str)
    current_frame = _extract_current_frame(frame_str)
    fields        = [f.strip() for f in fields_str.split(",") if f.strip()]

    parts = []
    for field_name in fields:
        value = _field(current_frame, field_name)
        if value:
            parts.append(f"{field_name}: {value}")
    return "\n".join(parts)

def dispatch_directive_args_from_metta(arg_string: str) -> str:
    """Bridge: parse directive args and return 'target|||task|||gate|||criteria|||priority'."""
    args = parse_directive_args(strip_metta(arg_string))
    return f"{args['target']}|||{args['task']}|||{args['gate']}|||{args['criteria']}|||{args['priority']}"

def directive_args_field(args_str: str, field: str) -> str:
    """Bridge: extract a field from the pipe-delimited args string."""
    mapping = {"target": 0, "task": 1, "gate": 2, "criteria": 3, "priority": 4}
    idx = mapping.get(field)
    if idx is None:
        return ""
    parts = str(args_str).split("|||", maxsplit=4)
    return parts[idx].strip() if idx < len(parts) else ""

def dispatch_directive_from_metta(arg_string: str, frame_str: str) -> str:
    """
    Single-string entry point called from attention.metta.
    Parses the argument string, builds the context slice, runs the
    full directive execution cycle, and returns the pipe-delimited result.

    max_attempts is derived from priority: priority >= 1.0 → 3 attempts,
    priority < 1.0 → 1 attempt (low-priority directives don't retry).
    """
    args         = parse_directive_args(strip_metta(arg_string))
    target       = args["target"]
    task         = args["task"]
    gate         = args["gate"]
    criteria     = args["criteria"]
    slice_fields = args["slice"]
    try:
        priority = float(args["priority"])
    except (ValueError, TypeError):
        priority = 1.0

    # priority >= 1.0 → full 3-attempt retry; lower priority → single attempt
    max_attempts = 3 if priority >= 1.0 else 1

    context = build_slice_from_metta(slice_fields, frame_str)
    if not context.strip():
        context = strip_metta(frame_str)

    return _run_directive_cycle(target, context, task, gate, criteria, max_attempts=max_attempts)

def execute_directive_from_metta(
    target: str, context: str, task: str,
    gate: str, criteria: str, max_retries: str
) -> str:
    """
    Bridge: run the full directive execution cycle including retry logic.
    max_retries is the number of *retries after the first attempt*, so
    max_attempts = max_retries + 1. Minimum is 1 attempt (0 retries).
    """
    target   = strip_metta(target)
    context  = strip_metta(context)
    task     = strip_metta(task)
    gate     = strip_metta(gate)
    criteria = strip_metta(criteria)
    try:
        max_attempts = max(1, int(strip_metta(max_retries)) + 1)
    except ValueError:
        max_attempts = 3
    return _run_directive_cycle(target, context, task, gate, criteria, max_attempts=max_attempts)


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
