"""LangGraph orchestration.

Full pipeline (built up incrementally across commits):

    Inspect -> Plan -> Generate -> Validate -> Review -> Finalize
                                       ^                    |
                                       |     Repair <--------  (fail, retries left)
                                       +------(loop)

This module owns the `AgentState` schema and every node function. Nodes are
coarse-grained — one per pipeline phase. Fine-grained work inside a phase
(e.g. generating N files in `generate`) is a plain Python loop inside that
node rather than N separate graph nodes: simpler to read, while LangGraph
still owns the phase-level flow and the Review -> Repair -> Validate retry
loop, which is the part that actually needs branching state.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

import prompts
import tools
from llm import LLMClientFactory
from llm import usage as llm_usage

logger = logging.getLogger("agent")


class AgentState(TypedDict):
    spec: str
    output_dir: str
    boilerplate_context: str
    tasks: list[dict]
    generated_files: dict[str, str]
    validation: dict
    review: dict
    retry_count: int
    max_retries: int


def _log(stage: str, message: str) -> None:
    logger.info(message, extra={"stage": stage})


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def inspect_node(state: AgentState) -> dict:
    """Read the scaffolded boilerplate once and build the shared context block.

    Runs a single time at the start of the pipeline so every later LLM call
    (plan, generate, review, repair) shares the same, real (not hardcoded)
    picture of the project instead of re-reading files or guessing.
    """
    root = Path(state["output_dir"])
    tree = "\n".join(tools.list_source_tree(root))
    types_ts = tools.read_file(root, "src/types.ts") or ""
    queries_ts = tools.read_file(root, "src/graphql/queries.ts") or ""
    example_tsx = tools.read_file(root, "src/components/Example.tsx") or ""
    example_test = tools.read_file(root, "src/__tests__/Example.test.tsx") or ""

    context = (
        f"File tree (src/):\n{tree}\n\n"
        f"src/types.ts:\n{types_ts}\n\n"
        f"src/graphql/queries.ts:\n{queries_ts}\n\n"
        f"Reference component pattern (src/components/Example.tsx) — follow this style:\n{example_tsx}\n\n"
        f"Reference test pattern (src/__tests__/Example.test.tsx) — follow this style:\n{example_test}"
    )
    _log("inspect", f"read {len(tree.splitlines())} files, built boilerplate context ({len(context)} chars)")
    return {"boilerplate_context": context}


def _parse_tasks(raw: str) -> list[dict]:
    """Extract and validate the task list from a plan reply.

    Raises ValueError/KeyError on anything malformed (missing `tasks`, a
    task without `path`/`description`, ...) so the caller can trigger one
    corrective retry instead of failing deep inside topo_sort/generate.
    """
    tasks = tools.extract_json(raw)["tasks"]
    if not isinstance(tasks, list) or not tasks:
        raise ValueError("`tasks` must be a non-empty list")
    for task in tasks:
        if not isinstance(task, dict) or "path" not in task or "description" not in task:
            raise ValueError(f"malformed task entry: {task!r}")
        task.setdefault("depends_on", [])
    return tasks


def plan_node(state: AgentState) -> dict:
    """Decompose the spec into an ordered, dependency-aware list of file tasks."""
    llm = LLMClientFactory.create("generator")
    system, user = prompts.plan_prompt(state["spec"], state["boilerplate_context"])
    _log("plan", "decomposing spec into ordered file tasks...")
    raw = llm.complete(system, user, role="generator")

    try:
        tasks = _parse_tasks(raw)
    except (ValueError, KeyError) as exc:
        # Local models occasionally drift from the schema (truncated JSON, a
        # renamed key, ...). One corrective retry, showing the model exactly
        # what was wrong, resolves this far more often than raising outright.
        _log("plan", f"invalid plan output ({exc}) — asking the model to correct it")
        raw = llm.complete(
            system,
            user + f"\n\nYour previous reply was invalid ({exc}). "
            "Respond with ONLY valid JSON matching the schema.",
            role="generator",
        )
        tasks = _parse_tasks(raw)

    tasks = tools.topo_sort(tasks)
    _log("plan", f"planned {len(tasks)} file(s): {', '.join(t['path'] for t in tasks)}")
    return {"tasks": tasks}


def generate_node(state: AgentState) -> dict:
    """Generate every planned file, in dependency order, feeding each file only
    the content of the dependency files it actually needs (bounded context,
    not the whole growing history)."""
    llm = LLMClientFactory.create("generator")
    root = Path(state["output_dir"])
    generated: dict[str, str] = {}

    for task in state["tasks"]:
        deps = {p: generated[p] for p in task.get("depends_on", []) if p in generated}
        system, user = prompts.generate_prompt(task, state["boilerplate_context"], deps)
        raw = llm.complete(system, user, role="generator")
        content = tools.extract_code_block(raw)
        tools.write_file(root, task["path"], content)
        generated[task["path"]] = content
        _log("generate", task["path"])

    return {"generated_files": generated}


def validate_node(state: AgentState) -> dict:
    """Run the generated app's own typecheck and test suite — for real.

    This is the mechanical half of self-validation (the LLM `review` node
    added in the next commit is the semantic half). No test framework is
    invented here: it's exactly `npm run typecheck` / `npm run test`,
    already wired up by the boilerplate.
    """
    root = Path(state["output_dir"])

    _log("validate", "npm run typecheck")
    tc_code, tc_out = tools.run_cmd(["npm", "run", "typecheck"], cwd=root)
    _log("validate", f"typecheck {'OK' if tc_code == 0 else 'FAIL'}")

    _log("validate", "npm run test")
    test_code, test_out = tools.run_cmd(["npm", "run", "test"], cwd=root)
    _log("validate", f"test {'OK' if test_code == 0 else 'FAIL'}")

    return {
        "validation": {
            "typecheck": {"ok": tc_code == 0, "output": tc_out},
            "test": {"ok": test_code == 0, "output": test_out},
        }
    }


def review_node(state: AgentState) -> dict:
    """Second LLM opinion: does the code actually satisfy the spec?

    The semantic half of self-validation, complementing the mechanical
    typecheck/test run — a file can pass both and still miss a requirement
    (e.g. the wrong breakpoint for the responsive image).
    """
    llm = LLMClientFactory.create("reviewer")
    system, user = prompts.review_prompt(state["spec"], state["generated_files"], state["validation"])
    _log("review", "checking generated files against the spec + validation output...")
    raw = llm.complete(system, user, role="reviewer")

    try:
        result = tools.extract_json(raw)
        passed, issues = bool(result["passed"]), result.get("issues", [])
    except (ValueError, KeyError) as exc:
        # An unparsable review is treated as a failed review, not a crash —
        # safer to loop through repair than to silently assume success.
        _log("review", f"invalid review output ({exc}) — treating as FAIL")
        passed, issues = False, [{"file": "", "problem": f"reviewer output was invalid: {exc}", "suggestion": ""}]

    _log("review", f"{'PASS' if passed else 'FAIL'} ({len(issues)} issue(s))")
    return {"review": {"passed": passed, "issues": issues}}


def _problem_files(state: AgentState) -> dict[str, str]:
    """Map path -> combined problem description, from validation output + review issues.

    tsc/vitest output is parsed for the file paths it actually mentions so
    only the implicated files are resent to the model; if nothing can be
    attributed, every generated file is treated as a candidate (safety net).
    """
    generated = state["generated_files"]
    problems: dict[str, list[str]] = {}

    for check_name, check in state["validation"].items():
        if check.get("ok"):
            continue
        output = check.get("output", "")
        targets = tools.files_with_errors(output) & set(generated) or set(generated)
        for path in targets:
            problems.setdefault(path, []).append(f"{check_name} output:\n{output[-2000:]}")

    for issue in state["review"].get("issues", []):
        path = issue.get("file")
        if path in generated:
            problems.setdefault(path, []).append(
                f"Reviewer: {issue.get('problem', '')} Suggestion: {issue.get('suggestion', '')}"
            )

    return {path: "\n\n".join(msgs) for path, msgs in problems.items()}


def repair_node(state: AgentState) -> dict:
    """Re-generate only the files implicated by validation/review failures."""
    llm = LLMClientFactory.create("generator")
    root = Path(state["output_dir"])
    generated = dict(state["generated_files"])
    problems = _problem_files(state) or {
        p: "Validation failed but no specific file could be blamed." for p in generated
    }

    attempt = state["retry_count"] + 1
    for path, problem_text in problems.items():
        system, user = prompts.repair_prompt(path, generated[path], problem_text, state["boilerplate_context"])
        raw = llm.complete(system, user, role="generator")
        content = tools.extract_code_block(raw)
        tools.write_file(root, path, content)
        generated[path] = content
        _log("repair", f"retry {attempt}/{state['max_retries']} -> {path}")

    return {"generated_files": generated, "retry_count": attempt}


def finalize_node(state: AgentState) -> dict:
    """Print the run's final outcome: what was built, whether it validated, at what cost."""
    validation = state.get("validation", {})
    tc_ok = validation.get("typecheck", {}).get("ok")
    test_ok = validation.get("test", {}).get("ok")
    review_ok = state.get("review", {}).get("passed")
    ok = bool(tc_ok and test_ok and review_ok)

    _log(
        "finalize",
        f"{'SUCCESS' if ok else 'FINISHED WITH OPEN ISSUES'} — "
        f"{len(state.get('generated_files', {}))} file(s), "
        f"typecheck={'OK' if tc_ok else 'FAIL'}, test={'OK' if test_ok else 'FAIL'}, "
        f"review={'PASS' if review_ok else 'FAIL'}, retries={state.get('retry_count', 0)}, "
        f"~{llm_usage.approx_tokens} tokens (~${llm_usage.approx_cost_usd})",
    )
    if not ok:
        for issue in state.get("review", {}).get("issues", []):
            _log("finalize", f"unresolved: {issue.get('file', '?')}: {issue.get('problem', '')}")
    return {}


def route_after_review(state: AgentState) -> str:
    """Conditional edge out of `review`: retry through `repair`, or stop at `finalize`."""
    validation_ok = all(check.get("ok") for check in state["validation"].values())
    review_ok = state["review"].get("passed", False)
    if validation_ok and review_ok:
        return "finalize"
    if state["retry_count"] >= state["max_retries"]:
        _log("route", f"retries exhausted ({state['retry_count']}/{state['max_retries']}) — finalizing as-is")
        return "finalize"
    return "repair"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("inspect", inspect_node)
    graph.add_node("plan", plan_node)
    graph.add_node("generate", generate_node)
    graph.add_node("validate", validate_node)
    graph.add_node("review", review_node)
    graph.add_node("repair", repair_node)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, "inspect")
    graph.add_edge("inspect", "plan")
    graph.add_edge("plan", "generate")
    graph.add_edge("generate", "validate")
    graph.add_edge("validate", "review")
    graph.add_conditional_edges("review", route_after_review, {"repair": "repair", "finalize": "finalize"})
    graph.add_edge("repair", "validate")
    graph.add_edge("finalize", END)

    return graph.compile()
