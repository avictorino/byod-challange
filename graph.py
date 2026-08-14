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


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


def build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("inspect", inspect_node)
    graph.add_node("plan", plan_node)
    graph.add_node("generate", generate_node)
    graph.add_node("validate", validate_node)

    graph.add_edge(START, "inspect")
    graph.add_edge("inspect", "plan")
    graph.add_edge("plan", "generate")
    graph.add_edge("generate", "validate")
    graph.add_edge("validate", END)

    return graph.compile()
