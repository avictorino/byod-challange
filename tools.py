"""Filesystem and parsing helpers shared by the graph nodes.

Deliberately free of LLM/LangGraph imports — plain, predictable utility
functions that graph.py composes.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

_SKIP_DIRS = {"node_modules", ".git", "dist", ".vite"}


# ---------------------------------------------------------------------------
# Scaffolding
# ---------------------------------------------------------------------------


def scaffold_output(boilerplate_dir: Path, output_dir: Path) -> None:
    """Copy the boilerplate into output_dir, replacing whatever was there."""
    if output_dir.exists():
        shutil.rmtree(output_dir)
    shutil.copytree(boilerplate_dir, output_dir, ignore=shutil.ignore_patterns(*_SKIP_DIRS))


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------


def read_file(root: Path, rel_path: str) -> str | None:
    path = root / rel_path
    return path.read_text(encoding="utf-8") if path.is_file() else None


def write_file(root: Path, rel_path: str, content: str) -> Path:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def list_source_tree(root: Path) -> list[str]:
    """Relative (repo-style, forward-slash) paths of every file under src/."""
    paths = []
    for p in sorted((root / "src").rglob("*")):
        if p.is_file() and not any(part in _SKIP_DIRS for part in p.parts):
            paths.append(str(p.relative_to(root)).replace("\\", "/"))
    return paths


# ---------------------------------------------------------------------------
# LLM output parsing — models don't always follow formatting instructions,
# so extraction is defensive rather than assuming a perfectly clean reply.
# ---------------------------------------------------------------------------

_CODE_FENCE_RE = re.compile(r"```(?:\w+)?\n(.*?)```", re.DOTALL)


def extract_code_block(text: str) -> str:
    """Pull the contents of the first fenced code block, or the raw text if none."""
    match = _CODE_FENCE_RE.search(text)
    body = match.group(1) if match else text
    return body.strip() + "\n"


def extract_json(text: str) -> dict:
    """Parse the first top-level {...} object found in text."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError(f"no JSON object found in LLM output: {text[:200]!r}")
    return json.loads(text[start : end + 1])


# ---------------------------------------------------------------------------
# Dependency ordering
# ---------------------------------------------------------------------------


def topo_sort(tasks: list[dict]) -> list[dict]:
    """Order tasks so each one comes after everything in its depends_on (Kahn's algorithm).

    Unknown or cyclic dependencies are dropped instead of raising — a
    best-effort ordering is safer than a hard crash on a mildly malformed plan.
    """
    by_path = {t["path"]: t for t in tasks}
    remaining = {t["path"]: {d for d in t.get("depends_on", []) if d in by_path} for t in tasks}
    ordered: list[dict] = []
    seen: set[str] = set()

    while remaining:
        ready = [p for p, deps in remaining.items() if deps <= seen] or list(remaining.keys())
        for path in ready:
            ordered.append(by_path[path])
            seen.add(path)
            del remaining[path]

    return ordered


# ---------------------------------------------------------------------------
# Shell commands — `npm install` / `npm run typecheck` / `npm run test` run
# for real inside the generated app; this is the mechanical half of
# self-validation (the LLM review in graph.py is the semantic half).
# ---------------------------------------------------------------------------


def run_cmd(cmd: list[str], cwd: Path, timeout: int = 600) -> tuple[int, str]:
    """Run a command, returning (exit_code, combined stdout+stderr).

    `shell=True` is needed on Windows to resolve `npm`; a timeout guards
    against a hung install/test run instead of blocking the pipeline forever.
    """
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        return 1, output + f"\n[timed out after {timeout}s]"
    return result.returncode, (result.stdout or "") + (result.stderr or "")
