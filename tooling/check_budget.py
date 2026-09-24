#!/usr/bin/env python3
"""Fail the build when a test layer exceeds its time budget.

A test pyramid does not collapse into an hourglass because someone decides
to write slow tests. It collapses because nobody owns the clock: each new
test adds a second, no single commit looks unreasonable, and a year later
the "fast" suite takes nine minutes.

Budgets make that a build failure with a name attached, which turns "should
this be an end-to-end test?" into a question with an enforced answer.

Usage:  python tooling/check_budget.py <junit.xml> <budget-seconds> [layer]
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# The contract for each layer. Raising one of these should be a deliberate,
# reviewed decision — that is the entire point of them living in git.
BUDGETS = {
    "unit": 15.0,
    "contract": 30.0,
    "component": 60.0,
    "journey": 300.0,
}


def total_seconds(junit_path: Path) -> tuple[float, int]:
    root = ET.parse(junit_path).getroot()
    suites = root.findall("testsuite") if root.tag == "testsuites" else [root]
    seconds = sum(float(s.get("time", 0) or 0) for s in suites)
    tests = sum(int(s.get("tests", 0) or 0) for s in suites)
    return seconds, tests


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2

    junit_path = Path(sys.argv[1])
    budget = float(sys.argv[2])
    layer = sys.argv[3] if len(sys.argv) > 3 else junit_path.stem

    if not junit_path.exists():
        print(f"::error::No JUnit report at {junit_path} — did the layer run?")
        return 1

    seconds, tests = total_seconds(junit_path)
    used = (seconds / budget * 100) if budget else 0
    summary = f"{layer}: {tests} tests in {seconds:.2f}s (budget {budget:.0f}s, {used:.0f}% used)"

    if seconds > budget:
        print(f"::error::{summary} — OVER BUDGET")
        print(
            "Either make the layer faster, or move the slow tests to a layer "
            "whose budget fits them. Raising the budget is a decision to argue "
            "for in review, not a reflex."
        )
        return 1

    # A layer coasting far under budget is also information: it usually
    # means coverage that could move left is sitting in a slower layer.
    if used < 20 and tests > 0:
        print(f"::notice::{summary} — comfortably inside budget")
    else:
        print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
