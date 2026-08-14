"""CLI entrypoint for the agentic code-generation workflow.

Usage:
    python agent.py --spec spec.txt --output generated-app

The actual generation pipeline lives in graph.py (a LangGraph StateGraph).
This module only handles argument parsing, .env loading, stage-tagged
logging, and printing the final summary.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

import tools
from graph import build_graph
from llm import LLMConfigError

STAGE_WIDTH = 8


class StageFormatter(logging.Formatter):
    """Renders log lines as `[HH:MM:SS] STAGE    message`.

    The stage name comes from `record.stage` (set via the `log()` helper
    below) so every line makes it obvious which part of the pipeline is
    currently running.
    """

    def format(self, record: logging.LogRecord) -> str:
        stage = getattr(record, "stage", record.levelname)
        record.msg = f"{stage.upper():<{STAGE_WIDTH}} {record.msg}"
        return super().format(record)


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("agent")
    logger.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StageFormatter("[%(asctime)s] %(msg)s", datefmt="%H:%M:%S"))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False
    return logger


def log(logger: logging.Logger, stage: str, message: str) -> None:
    """Log one line tagged with the current pipeline stage (PLAN, GENERATE, ...)."""
    logger.info(message, extra={"stage": stage})


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Agentic workflow that generates a React+TS app from a natural-language spec.",
    )
    parser.add_argument("--spec", default="spec.txt", help="Path to the natural-language spec file.")
    parser.add_argument("--output", default="generated-app", help="Output directory for the generated app.")
    parser.add_argument(
        "--boilerplate",
        default="Fullstack-Coding-Challenge-main",
        help="Path to the boilerplate project to scaffold from.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=2,
        help="Max repair attempts if validation/review fails (default: 2).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv()
    logger = setup_logging()

    spec_path = Path(args.spec)
    boilerplate_path = Path(args.boilerplate)
    output_path = Path(args.output)

    if not spec_path.is_file():
        log(logger, "error", f"spec file not found: {spec_path}")
        return 1
    if not boilerplate_path.is_dir():
        log(logger, "error", f"boilerplate directory not found: {boilerplate_path}")
        return 1

    log(
        logger,
        "start",
        f"spec={spec_path} output={output_path} boilerplate={boilerplate_path} "
        f"max_retries={args.max_retries}",
    )

    app = build_graph()
    initial_state = {
        "spec": spec_path.read_text(encoding="utf-8"),
        "output_dir": str(output_path),
        "boilerplate_context": "",
        "tasks": [],
        "generated_files": {},
        "validation": {},
        "review": {},
        "retry_count": 0,
        "max_retries": args.max_retries,
        "escalated": False,
    }

    try:
        log(logger, "scaffold", f"copying {boilerplate_path} -> {output_path}")
        tools.scaffold_output(boilerplate_path, output_path)

        log(logger, "scaffold", "npm install (this can take a minute)...")
        install_code, install_out = tools.run_cmd(["npm", "install"], cwd=output_path, timeout=600)
        if install_code != 0:
            log(logger, "error", f"npm install failed:\n{install_out[-2000:]}")
            return 1

        final_state = app.invoke(initial_state)
    except LLMConfigError as exc:
        log(logger, "error", f"configuration problem: {exc}")
        return 1
    except Exception as exc:  # last resort: report cleanly instead of a raw traceback
        log(logger, "error", f"pipeline failed: {exc}")
        return 1

    # finalize_node already logged the full summary (files, validation, review,
    # retries, approx cost) — just decide the process exit code from it.
    validation = final_state.get("validation", {})
    ok = (
        all(check.get("ok") for check in validation.values())
        and final_state.get("review", {}).get("passed", False)
    )
    log(logger, "done", f"run `cd {output_path} && npm run dev` to try the app")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
