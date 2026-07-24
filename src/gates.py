"""
Admission gates for HyperClaw attention directives.

Each gate is a function: (output, task, criteria) -> GateResult
- Structural/parseability: python-syntax, metta-syntax
- Execution:               exec-success
- Numerical plausibility:  math-result  (range-aware via criteria)
- Task-consistency:        task-consistency (output structure matches task type)
- Minimal:                 nonempty, passthrough
"""
import ast
import re
from src.helper import strip_code_fences, strip_metta


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
    """
    Check that the output contains a standalone numerical result and that it
    falls within any range specified in the criteria string.

    Criteria examples (all optional):
      "between 0 and 1"   -> 0 <= value <= 1
      "> 0"               -> value > 0
      ">= 0.5"            -> value >= 0.5
      "< 100"             -> value < 100
      "<= 10"             -> value <= 10
    If no range is specified in criteria, only presence is checked.
    """
    # Require number to be a standalone token — avoids matching dates or version strings.
    pattern = r"(?<![\w-])-?\d[\d,.]*(?![\w-])"
    matches = re.findall(pattern, output.strip())
    if not matches:
        return GateResult(
            False,
            "No numerical result found",
            f"Expected a number in the response but got: '{output[:100]}'"
        )

    # Parse the first number found
    try:
        value = float(matches[0].replace(",", ""))
    except ValueError:
        return GateResult(True, f"Numerical result found: {matches[0]}")

    # Range check from criteria
    if criteria and criteria.strip():
        c = criteria.strip().lower()

        # "between X and Y"
        m = re.search(r"between\s+([-\d.]+)\s+and\s+([-\d.]+)", c)
        if m:
            lo, hi = float(m.group(1)), float(m.group(2))
            if not (lo <= value <= hi):
                return GateResult(
                    False,
                    f"Value {value} outside range [{lo}, {hi}]",
                    f"Expected a value between {lo} and {hi} but got {value}"
                )
            return GateResult(True, f"Numerical result {value} within [{lo}, {hi}]")

        # ">= X", "> X", "<= X", "< X"
        for op_str, op_re in [(">=", r">=\s*([-\d.]+)"), (">", r"(?<!<)(?<!>)>\s*([-\d.]+)"),
                               ("<=", r"<=\s*([-\d.]+)"), ("<", r"(?<!<)(?<!>)<\s*([-\d.]+)")]:
            m = re.search(op_re, c)
            if m:
                bound = float(m.group(1))
                ops = {">=": value >= bound, ">": value > bound,
                       "<=": value <= bound, "<": value < bound}
                if not ops[op_str]:
                    return GateResult(
                        False,
                        f"Value {value} does not satisfy {op_str} {bound}",
                        f"Expected value {op_str} {bound} but got {value}"
                    )
                return GateResult(True, f"Numerical result {value} satisfies {op_str} {bound}")

    return GateResult(True, f"Numerical result found: {matches[0]}")


def gate_nonempty(output: str, task: str, criteria: str) -> GateResult:
    """Minimal gate — just checks the response is non-empty."""
    if output and output.strip():
        return GateResult(True, "Non-empty response")
    return GateResult(False, "Empty response", "Module returned an empty string")


def gate_exec_success(output: str, task: str, criteria: str) -> GateResult:
    """Gate for execution output — fails if empty or subprocess errored."""
    if not output or not output.strip():
        return GateResult(False, "Empty execution output", "Executor returned no output")
    if output.startswith("MODULE_ERROR:"):
        return GateResult(False, "Execution failed", output[:300])
    return GateResult(True, "Execution succeeded")


def gate_passthrough(output: str, task: str, criteria: str) -> GateResult:
    """Always admits. Used when no gate is needed."""
    return GateResult(True, "Passthrough gate — always admits")


# Expected structural markers per task type.
# A task-consistency check passes if at least one marker for the task is present.
_TASK_MARKERS: dict[str, list[str]] = {
    "Critique":  ["OVERALL:", "WEAKNESSES:", "ISSUES:", "VERDICT:"],
    "Evaluate":  ["OVERALL:", "SCORE:", "STRENGTHS:", "WEAKNESSES:", "VERDICT:"],
    "Revise":    ["REVISED", "CHANGES:", "REVISION:"],
    "Generate":  [],   # no structural requirement — nonempty is sufficient
    "Execute":   [],   # execution output has no required structure
}


def gate_task_consistency(output: str, task: str, criteria: str) -> GateResult:
    """
    Check that the output structure is consistent with the task type.

    Critique/Evaluate outputs must contain at least one expected section header.
    Revise outputs must contain a revision marker.
    Generate/Execute outputs only need to be non-empty.
    """
    if not output or not output.strip():
        return GateResult(False, "Empty output", "Module returned an empty string")

    markers = _TASK_MARKERS.get(task, [])
    if not markers:
        # Generate and Execute: non-empty is sufficient
        return GateResult(True, f"Task {task}: non-empty output accepted")

    upper = output.upper()
    found = [m for m in markers if m in upper]
    if found:
        return GateResult(True, f"Task {task}: found expected markers {found}")

    return GateResult(
        False,
        f"Task {task}: output missing expected structure",
        f"Expected at least one of {markers} in the output for a {task} task. "
        f"Got: '{output[:150]}'"
    )


def gate_certified_method(output: str, task: str, criteria: str) -> GateResult:
    """
    Check that the output is consistent with the frame's certified method.

    The criteria string must contain the certified method description extracted
    from the frame (passed by attention.metta via the criteria field).
    Format: "method: <description>" — attention.metta extracts this from
    cfv2-current-method and passes it as the criteria argument.

    If no method description is provided in criteria, the gate passes
    (no certified method is set for this frame).

    This implements the paper's "preventing the forgotten method" guarantee:
    the output must reference key terms from the certified method description.
    """
    if not criteria or not criteria.strip():
        return GateResult(True, "No certified method set — gate skipped")

    # Extract method description from criteria string
    c = criteria.strip()
    if c.lower().startswith("method:"):
        method_desc = c[len("method:"):].strip()
    else:
        method_desc = c

    if not method_desc:
        return GateResult(True, "No certified method description — gate skipped")

    # Extract key terms from the method description (words > 4 chars, skip stopwords)
    _STOPWORDS = {"with", "that", "this", "from", "using", "based", "which", "where",
                  "their", "have", "been", "will", "should", "would", "could"}
    key_terms = [
        w.lower() for w in re.findall(r"\b[a-zA-Z]{5,}\b", method_desc)
        if w.lower() not in _STOPWORDS
    ]

    if not key_terms:
        return GateResult(True, "Certified method has no extractable key terms — gate skipped")

    output_lower = output.lower()
    matched = [t for t in key_terms if t in output_lower]
    coverage = len(matched) / len(key_terms)

    # Require at least 30% of key terms to appear in the output
    if coverage >= 0.3:
        return GateResult(
            True,
            f"Certified method consistency: {len(matched)}/{len(key_terms)} key terms present"
        )

    missing = [t for t in key_terms if t not in output_lower][:5]
    return GateResult(
        False,
        f"Certified method consistency: only {len(matched)}/{len(key_terms)} key terms present",
        f"Output may not reference the certified method. "
        f"Missing key terms: {missing}. "
        f"Ensure the output addresses: {method_desc[:200]}"
    )


_GATES = {
    "python-syntax":      gate_python_syntax,
    "metta-syntax":       gate_metta_syntax,
    "math-result":        gate_math_result,
    "nonempty":           gate_nonempty,
    "exec-success":       gate_exec_success,
    "task-consistency":   gate_task_consistency,
    "passthrough":        gate_passthrough,
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

    if "executor" in target_lower:
        return "exec-success"
    if "python" in target_lower:
        return "python-syntax"
    if "metta" in target_lower:
        return "metta-syntax"
    if task_lower == "execute":
        return "exec-success"
    if "math" in task_lower or "arithmetic" in task_lower or "calculate" in task_lower:
        return "math-result"
    if task_lower in {"critique", "evaluate"}:
        return "task-consistency"
    return "nonempty"


def run_gate_from_metta(gate: str, output: str, task: str, criteria: str) -> str:
    """Bridge: run a gate and return 'passed|||reason|||detail' string."""
    result = run_gate(
        strip_metta(gate),
        strip_metta(output),
        strip_metta(task),
        strip_metta(criteria),
    )
    passed = "True" if result.passed else "False"
    return f"{passed}|||{result.reason}|||{result.detail}"


def infer_gate_from_metta(task: str, target: str) -> str:
    """Bridge: infer gate name from task and target strings."""
    return infer_gate(strip_metta(task), strip_metta(target))
