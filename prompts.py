"""Prompt templates for each LLM-calling node.

Every prompt (1) embeds the real boilerplate context assembled once by
`inspect_node` — never re-derived or hardcoded per call, (2) forces a
specific output format (JSON or a single fenced code block) so graph.py can
parse the reply deterministically, and (3) for code generation, carries the
boilerplate's own Example.tsx / Example.test.tsx as few-shot references so
generated code matches the existing style instead of inventing a new one.
"""
from __future__ import annotations

CONVENTIONS = """\
Project conventions (do not violate these):
- React 19 + TypeScript, Vite, Apollo Client 3, Material UI 6.
- TypeScript is strict, with noUnusedLocals, noUnusedParameters and
  noUncheckedIndexedAccess enabled: no unused imports/vars, and guard any
  indexed array/object access that could be undefined.
- The project uses the new JSX transform (tsconfig `jsx: react-jsx`): do NOT
  `import React from "react"` unless you actually use the `React` namespace
  (e.g. `React.FormEvent`). Prefer named imports (`useState`, `useEffect`,
  and `type FormEvent`, `type ChangeEvent`, ...) instead — an unused default
  React import fails the strict noUnusedLocals check.
- MUI v6 `Select`'s `onChange` is typed `(event: SelectChangeEvent<T>, child:
  ReactNode) => void` — import `SelectChangeEvent` from "@mui/material" and
  type handlers with it. Do NOT use the old MUI v4 pattern
  `(event: React.ChangeEvent<{ value: unknown }>) => void`; it does not
  typecheck against this version.
- Generate exactly one file with exactly one default export (or the named
  exports its task describes). Never bundle a second component/module into
  the same reply, and never emit more than one `export default` — if a small
  helper sub-component is needed, define it unexported in the same file.
- Import local modules with the `@/` alias (maps to `src/`), e.g. `@/types`,
  `@/graphql/queries`, `@/components/CarCard`.
- Only use GraphQL queries/mutations and fields already defined in
  queries.ts and types.ts (shown below) — never invent new ones.
- Never modify src/graphql/*, src/mocks/*, src/types.ts or src/test-setup.ts;
  they already satisfy the spec and are off-limits.
"""


def plan_prompt(spec: str, boilerplate_context: str) -> tuple[str, str]:
    system = (
        "You are a senior React/TypeScript architect. You decompose a product "
        "spec into an ordered list of file-level implementation tasks for an "
        "existing Vite + Apollo + MUI boilerplate. Respond with ONLY a JSON "
        "object — no prose, no markdown fences.\n\n"
        "JSON schema:\n"
        '{"tasks": [{"path": "src/...", "description": "...", "depends_on": ["src/..."]}]}\n\n'
        "Rules:\n"
        "- Only list files to create or replace under src/ (components/, "
        "hooks/, __tests__/, or App.tsx). Never plan changes to src/graphql/*, "
        "src/mocks/*, src/types.ts or src/test-setup.ts.\n"
        "- depends_on lists the paths of OTHER planned files this file imports from.\n"
        "- Include at least one file under src/__tests__/ covering a key component.\n"
        "- Keep the list minimal: one file per responsibility, no dead files.\n\n"
        "Example for a DIFFERENT, simpler spec — copy the SHAPE, not the content:\n"
        '{"tasks": [\n'
        '  {"path": "src/hooks/useWidgets.ts", "description": "Wrap the GetWidgets '
        'query in a hook returning {widgets, loading, error}", "depends_on": []},\n'
        '  {"path": "src/components/WidgetCard.tsx", "description": "MUI card '
        'showing one widget", "depends_on": []},\n'
        '  {"path": "src/App.tsx", "description": "Fetch via useWidgets and render '
        'a WidgetCard per item", "depends_on": ["src/hooks/useWidgets.ts", '
        '"src/components/WidgetCard.tsx"]}\n'
        "]}"
    )
    user = (
        f"{CONVENTIONS}\n"
        f"Boilerplate context:\n{boilerplate_context}\n\n"
        f"Product spec:\n{spec}\n\n"
        "Produce the task list JSON now."
    )
    return system, user


def generate_prompt(
    task: dict,
    boilerplate_context: str,
    dependency_files: dict[str, str],
) -> tuple[str, str]:
    system = (
        "You are a senior React/TypeScript engineer. Generate the COMPLETE "
        "contents of exactly one file for a Vite + Apollo Client + MUI + "
        "TypeScript-strict project. Respond with ONLY a single fenced code "
        "block containing the file's full contents — no explanation before or after it."
    )
    deps_block = (
        "\n\n".join(
            f"--- {path} (already generated — import from it, do not redefine it) ---\n{content}"
            for path, content in dependency_files.items()
        )
        or "(no dependency files for this task)"
    )
    user = (
        f"{CONVENTIONS}\n"
        f"Boilerplate context:\n{boilerplate_context}\n\n"
        f"Already-generated dependency files:\n{deps_block}\n\n"
        f"Task: generate `{task['path']}`\n"
        f"Description: {task['description']}\n\n"
        "Output the file contents now, inside one ```tsx or ```ts code block."
    )
    return system, user


def _tail(text: str, n: int = 3000) -> str:
    return text[-n:] if len(text) > n else text


def review_prompt(spec: str, generated_files: dict[str, str], validation: dict) -> tuple[str, str]:
    system = (
        "You are a meticulous senior code reviewer for a React + TypeScript + "
        "Apollo Client + MUI project. Review the generated files against the "
        "product spec and the mechanical validation output below. Respond "
        "with ONLY a JSON object — no prose, no markdown fences.\n\n"
        'JSON schema: {"passed": bool, "issues": [{"file": "src/...", '
        '"problem": "...", "suggestion": "..."}]}\n\n'
        "Rules:\n"
        "- If typecheck or test FAILED, passed MUST be false, and issues MUST "
        "cover the file(s) responsible for those failures.\n"
        "- Otherwise passed is true unless you find a real functional defect "
        "against the spec — not a style nitpick.\n"
        "- Every issue must name an actual file from the list below."
    )
    files_block = "\n\n".join(f"--- {path} ---\n{content}" for path, content in generated_files.items())
    tc = validation.get("typecheck", {})
    ts = validation.get("test", {})
    user = (
        f"Product spec:\n{spec}\n\n"
        f"Generated files:\n{files_block}\n\n"
        f"typecheck: {'PASS' if tc.get('ok') else 'FAIL'}\n{_tail(tc.get('output', ''))}\n\n"
        f"test: {'PASS' if ts.get('ok') else 'FAIL'}\n{_tail(ts.get('output', ''))}\n\n"
        "Produce the review JSON now."
    )
    return system, user


def repair_prompt(
    path: str,
    current_content: str,
    problems: str,
    boilerplate_context: str,
) -> tuple[str, str]:
    system = (
        "You are a senior React/TypeScript engineer fixing a single file "
        "based on real validation errors. Make the SMALLEST change that "
        "fixes the listed problems — preserve the file's existing structure, "
        "exports and overall approach unless a problem specifically requires "
        "changing them. Do not rewrite the file from scratch or switch to a "
        "different pattern. Respond with ONLY a single fenced code block "
        "containing the file's COMPLETE corrected contents — no explanation "
        "before or after it."
    )
    user = (
        f"{CONVENTIONS}\n"
        f"Boilerplate context:\n{boilerplate_context}\n\n"
        f"File to fix: `{path}`\n\n"
        f"Current contents:\n```\n{current_content}\n```\n\n"
        f"Problems to fix:\n{problems}\n\n"
        "Output the corrected, complete file contents now."
    )
    return system, user
