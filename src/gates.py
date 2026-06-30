"""
Admission gates for HyperClaw attention directives.

Each gate is a function: (output, task, criteria) -> GateResult
- Code tasks:     mechanical check (ast.parse for Python, paren balance for MeTTa)
- Non-code tasks: LLM-as-judge using the success criteria sentence
"""
import ast
import re
from src.helper import strip_code_fences
from lib_llm_ext import callActiveProvider, callJudgeProvider


class GateResult:
    def __init__(self, passed: bool, reason: str, detail: str = ""):
        self.passed = passed
        self.reason = reason
        # detail is the raw error info appended to the retry prompt
        self.detail = detail or reason

    def __repr__(self):
        status = "PASSED" if self.passed else "FAILED"
        return f"GateResult({status}: {self.reason})"

    def retry_feedback(self) -> str:
        """What gets appended to the retry prompt when gate fails."""
        return f"GATE_FAILED: {self.detail}"

def gate_python_syntax(output: str, task: str, criteria: str) -> GateResult:
    """Check Python code syntax using ast.parse."""
    code = strip_code_fences(output)
    if not code.strip():
        return GateResult(False, "Empty Python output", "Module returned empty code — generate actual Python code")
    try:
        ast.parse(code)
        return GateResult(True, "Python syntax valid")
    except SyntaxError as e:
        detail = f"SyntaxError at line {e.lineno}, col {e.offset}: {e.msg}"
        return GateResult(False, "Python syntax invalid", detail)
    except Exception as e:
        return GateResult(False, "Python parse error", str(e))

def gate_metta_syntax(output: str, task: str, criteria: str) -> GateResult:
    """Check MeTTa syntax: non-empty, starts with '(', balanced parentheses outside strings."""
    code = strip_code_fences(output).strip()
    if not code:
        return GateResult(False, "Empty MeTTa output", "Output is empty")

    open_count = 0
    close_count = 0
    in_string = False
    escaped = False
    for ch in code:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if not in_string:
            if ch == "(":
                open_count += 1
            elif ch == ")":
                close_count += 1

    if open_count != close_count:
        detail = (
            f"Unbalanced parentheses: {open_count} opening, "
            f"{close_count} closing. "
            f"Difference: {abs(open_count - close_count)}"
        )
        return GateResult(False, "MeTTa syntax invalid", detail)

    if not code.startswith("("):
        detail = f"MeTTa expression must start with '(' but got: '{code[:30]}'"
        return GateResult(False, "MeTTa syntax invalid", detail)

    return GateResult(True, "MeTTa syntax valid")

def gate_math_result(output: str, task: str, criteria: str) -> GateResult:
    """Check that the output contains a standalone numerical result."""
    # Require number to be a standalone token — preceded and followed by
    # whitespace, start/end of string, or punctuation. This avoids matching
    # dates (2026-06-27), version strings (v1.0.3), or frame IDs.
    pattern = r"(?<![\w-])-?\d[\d,.]*(?![\w-])"
    matches = re.findall(pattern, output.strip())
    if matches:
        return GateResult(True, f"Numerical result found: {matches[0]}")
    return GateResult(
        False,
        "No numerical result found",
        f"Expected a number in the response but got: '{output[:100]}'"
    )

def gate_nonempty(output: str, task: str, criteria: str) -> GateResult:
    """Minimal gate — just checks the response is non-empty."""
    if output and output.strip():
        return GateResult(True, "Non-empty response")
    return GateResult(False, "Empty response", "Module returned an empty string")

def gate_passthrough(output: str, task: str, criteria: str) -> GateResult:
    """Always admits. Used when no gate is needed."""
    return GateResult(True, "Passthrough gate — always admits")

def gate_llm_judge(output: str, task: str, criteria: str) -> GateResult:
    """
    Use an LLM to evaluate whether the output satisfies the success criteria.
    The criteria is a single sentence describing the desired outcome.
    Falls back to nonempty if no criteria provided or LLM call fails.
    Uses whichever provider the loop is currently configured to use.
    """
    if not criteria or not criteria.strip():
        return gate_nonempty(output, task, criteria)

    try:
        truncated = output[:3000] if len(output) > 3000 else output
        judge_prompt = (
            f"Evaluate whether the output satisfies the success criteria.\n"
            f"Respond using EXACTLY this format and nothing else:\n"
            f"Verdict: PASS\n"
            f"Reason: <one sentence>\n"
            f"--- or ---\n"
            f"Verdict: FAIL\n"
            f"Reason: <one sentence>\n\n"
            f"SUCCESS CRITERIA: {criteria}\n\n"
            f"OUTPUT:\n{truncated}\n\n"
            f"Verdict:"
        )

        response = callJudgeProvider(judge_prompt, max_tokens=150)
        if not response or not response.strip():
            return gate_nonempty(output, task, criteria)

        # Reconstruct the full response with the priming token the model was completing
        full = ("Verdict:" + response).strip()

        verdict = ""
        reason = "No reason given"
        for i, line in enumerate(full.splitlines()):
            upper = line.upper()
            if "PASS" in upper and "FAIL" not in upper:
                verdict = "PASS"
                rest = " ".join(full.splitlines()[i+1:]).replace("Reason:", "").strip()
                reason = rest or "No reason given"
                break
            elif "FAIL" in upper:
                verdict = "FAIL"
                rest = " ".join(full.splitlines()[i+1:]).replace("Reason:", "").strip()
                reason = rest or "No reason given"
                break

        if verdict == "PASS":
            return GateResult(True, f"LLM judge: PASS — {reason}")
        elif verdict == "FAIL":
            detail = (
                f"LLM judge: FAIL — {reason}\n"
                f"Criteria was: {criteria}"
            )
            return GateResult(False, f"LLM judge: FAIL — {reason}", detail)
        else:
            print(f"[gate_llm_judge] No PASS/FAIL in response: {repr(full[:200])}")
            return GateResult(True, f"LLM judge: ambiguous response, admitting")

    except Exception as e:
        print(f"[gate_llm_judge] Exception: {e}")
        return gate_nonempty(output, task, criteria)

_GATES = {
    "python-syntax": gate_python_syntax,
    "metta-syntax":  gate_metta_syntax,
    "math-result":   gate_math_result,
    "nonempty":      gate_nonempty,
    "llm-judge":     gate_llm_judge,
    "passthrough":   gate_passthrough,
}

def run_gate(gate_name: str, output: str, task: str, criteria: str = "") -> GateResult:
    """
    Dispatch to the named gate.
    Unknown gate names return a failed GateResult so the caller knows
    the intended gate never ran — prevents silent wrong-gate admission.
    """
    fn = _GATES.get(gate_name)
    if fn is None:
        known = ", ".join(_GATES.keys())
        return GateResult(
            False,
            f"Unknown gate '{gate_name}'",
            f"Unknown gate '{gate_name}'. Available: {known}"
        )
    return fn(output, task, criteria)

def infer_gate(task: str, target: str) -> str:
    """Infer the appropriate gate from task and target when not explicitly set."""
    task_lower = task.lower()
    target_lower = target.lower()

    if "python" in target_lower or "executor" in target_lower:
        return "python-syntax"
    if "metta" in target_lower:
        return "metta-syntax"
    if task_lower == "execute":
        return "nonempty"
    if "math" in task_lower or "arithmetic" in task_lower or "calculate" in task_lower:
        return "math-result"
    # Default for Generate, Critique, Revise, Evaluate with no specific target
    return "llm-judge"

def run_gate_from_metta(gate: str, output: str, task: str, criteria: str) -> str:
    """Bridge: run a gate and return 'passed|||reason|||detail' string."""
    gate     = gate.strip().strip("'\"")
    output   = output.strip().strip("'\"")
    task     = task.strip().strip("'\"")
    criteria = criteria.strip().strip("'\"")
    result   = run_gate(gate, output, task, criteria)
    passed   = "True" if result.passed else "False"
    return f"{passed}|||{result.reason}|||{result.detail}"

def infer_gate_from_metta(task: str, target: str) -> str:
    """Bridge: infer gate name from task and target strings."""
    return infer_gate(
        task.strip().strip("'\""),
        target.strip().strip("'\"")
    )
