from typing import TypedDict, Any, Optional


DEFAULT_MAX_ROUNDS = 5


class BreakpointState(TypedDict):
    request: str
    spec: dict
    code: str
    pending_tests: list
    all_test_codes: list
    tests: list
    round: int
    max_rounds: int
    round_passed: bool
    prior_failure: Optional[dict]
    verdict: Optional[str]
    history: list
    report: Optional[dict]
