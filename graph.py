"""LangGraph wiring: framer -> prover -> skeptic -> sandbox -> (loop) -> arbiter.

The retry loop (V3): after sandbox_node, if any test failed and the round
budget isn't exhausted, control returns to prover_node with the failure
trace. Every round re-runs ALL previously-generated tests, not just the
newest ones, so a fix can't silently reintroduce an old bug. arbiter_node
(V4) produces the final verdict/coverage/confidence report.
"""

from langgraph.graph import StateGraph, END

from state import BreakpointState
from nodes.framer import frame
from nodes.prover import prove
from nodes.skeptic import find_bugs
from nodes.arbiter import arbitrate
from sandbox import run_test


def framer_node(state: BreakpointState) -> dict:
    spec = frame(state["request"])
    return {"spec": spec}


def prover_node(state: BreakpointState) -> dict:
    prior_failure = state.get("prior_failure")
    round_num = state["round"]
    if prior_failure:
        # A retry: this is the "increment round" step the plan calls for,
        # done here (rather than in a separate node) since the round only
        # advances when the Prover is about to attempt a fix.
        round_num += 1

    code = prove(state["spec"], prior_failure=prior_failure)
    return {"code": code, "round": round_num}


def skeptic_node(state: BreakpointState) -> dict:
    new_tests = find_bugs(state["spec"], state["code"])
    return {"pending_tests": new_tests}


def sandbox_node(state: BreakpointState) -> dict:
    all_test_codes = list(state.get("all_test_codes", []))
    for test_code in state["pending_tests"]:
        if test_code not in all_test_codes:
            all_test_codes.append(test_code)

    results = []
    for test_code in all_test_codes:
        result = run_test(state["code"], test_code)
        # result.error is a short category ("timed out", "exited with code N")
        # while result.stderr carries the actual traceback/assertion message —
        # the Prover needs the latter to fix anything precisely.
        diagnostic = None
        if not result.passed:
            diagnostic = result.stderr.strip() if result.stderr.strip() else result.error
        results.append(
            {
                "test_code": test_code,
                "passed": result.passed,
                "error": diagnostic,
            }
        )

    round_passed = all(r["passed"] for r in results)
    first_failure = next((r for r in results if not r["passed"]), None)

    record = {
        "round": state["round"],
        "code": state["code"],
        "new_tests": state["pending_tests"],
        "results": results,
        "round_passed": round_passed,
    }
    history = state.get("history", []) + [record]

    return {
        "all_test_codes": all_test_codes,
        "tests": results,
        "history": history,
        "round_passed": round_passed,
        "prior_failure": first_failure,
    }


def route_after_sandbox(state: BreakpointState) -> str:
    if state["round_passed"]:
        return "arbiter"
    if state["round"] < state["max_rounds"]:
        return "prover"
    return "arbiter"  # round budget exhausted, unresolved


def arbiter_node(state: BreakpointState) -> dict:
    report = arbitrate(state)
    return {"verdict": report["verdict"], "report": report}


def build_graph():
    graph = StateGraph(BreakpointState)
    graph.add_node("framer", framer_node)
    graph.add_node("prover", prover_node)
    graph.add_node("skeptic", skeptic_node)
    graph.add_node("sandbox", sandbox_node)
    graph.add_node("arbiter", arbiter_node)

    graph.set_entry_point("framer")
    graph.add_edge("framer", "prover")
    graph.add_edge("prover", "skeptic")
    graph.add_edge("skeptic", "sandbox")
    graph.add_conditional_edges(
        "sandbox", route_after_sandbox, {"prover": "prover", "arbiter": "arbiter"}
    )
    graph.add_edge("arbiter", END)

    return graph.compile()
