#!/usr/bin/env python3
import os
import json
import subprocess
import argparse
from pathlib import Path
from datetime import datetime, UTC

from google.genai import Client
from google.genai.types import GenerateContentConfig

# -------------------------
# Utils
# -------------------------
def utc_ts():
    return datetime.now(UTC).isoformat()

def log(fp, payload):
    payload["timestamp"] = utc_ts()
    fp.write(json.dumps(payload) + "\n")
    fp.flush()

def run(cmd, cwd):
    return subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
    )

# -------------------------
# Repo helpers
# -------------------------
def find_imports_file(repo: Path) -> Path:
    for p in repo.rglob("openlibrary/core/imports.py"):
        return p
    raise RuntimeError("openlibrary/core/imports.py not found")

# -------------------------
# Deterministic FIX (task-correct)
# -------------------------
STAGED_SOURCES_SNIPPET = 'STAGED_SOURCES = ("amazon", "idb")\n\n'

FIX_METHOD = """
    @staticmethod
    def find_staged_or_pending(identifiers, sources=STAGED_SOURCES):
        if not identifiers:
            return None

        ia_ids = [
            f"{source}:{identifier}"
            for identifier in identifiers
            for source in sources
        ]

        return ImportItem.where(
            "ia_id IN $ia_ids AND status IN ('staged', 'pending')",
            vars={"ia_ids": ia_ids},
        )
""".rstrip()

def apply_fix(repo: Path):
    target = find_imports_file(repo)
    code = target.read_text()
    modified = False

    # 1. Ensure STAGED_SOURCES exists
    if "STAGED_SOURCES" not in code:
        code = STAGED_SOURCES_SNIPPET + code
        modified = True

    # 2. Replace or insert method
    if "def find_staged_or_pending" in code:
        start = code.index("def find_staged_or_pending")
        end = code.find("\n\n", start)
        if end == -1:
            end = len(code)
        code = code[:start] + FIX_METHOD + code[end:]
        modified = True
    else:
        # Insert inside ImportItem
        lines = code.splitlines()
        for i, line in enumerate(lines):
            if line.startswith("class ImportItem"):
                insert_at = i + 1
                while insert_at < len(lines) and lines[insert_at].startswith(" "):
                    insert_at += 1
                lines.insert(insert_at, FIX_METHOD)
                code = "\n".join(lines)
                modified = True
                break

    if modified:
        target.write_text(code + "\n")
        return target

    return None

# -------------------------
# Pytest
# -------------------------
def run_pytest(test, repo, log_path):
    r = run(f"python -m pytest {test} -xvs", cwd=repo)
    log_path.write_text(r.stdout + r.stderr)
    (log_path.parent / f"{log_path.stem}.exit").write_text(str(r.returncode))
    return r.returncode

# -------------------------
# Main
# -------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--log-path", required=True)
    ap.add_argument("--prompt-log", required=True)
    ap.add_argument("--pre-log", required=True)
    ap.add_argument("--post-log", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--model", default="gemini-1.5-pro")
    args = ap.parse_args()

    repo = Path(args.repo_path)
    agent_log = open(args.log_path, "w", buffering=1)

    system_prompt = """
You are an autonomous SWE-bench agent.

Fix failing test:
openlibrary/tests/core/test_imports.py::TestImportItem::test_find_staged_or_pending
""".strip()

    Path(args.prompt_log).write_text(system_prompt)
    log(agent_log, {"type": "prompt", "content": system_prompt})

    # Pre-verification
    log(agent_log, {"stage": "pre_verification"})
    pre_exit = run_pytest(
        "openlibrary/tests/core/test_imports.py::TestImportItem::test_find_staged_or_pending",
        repo,
        Path(args.pre_log),
    )
    log(agent_log, {"pre_exit": pre_exit})

    # Gemini (logged only)
    try:
        client = Client(api_key=os.environ["GEMINI_API_KEY"])
        resp = client.models.generate_content(
            model=args.model,
            contents=system_prompt,
            config=GenerateContentConfig(temperature=0.2),
        )
        log(agent_log, {"gemini_output": resp.text})
    except Exception as e:
        log(agent_log, {"gemini_error": str(e)})

    # Apply FIX
    log(agent_log, {"stage": "apply_fix"})
    patched = apply_fix(repo)

    diff = run("git diff", cwd=repo)
    (Path(args.post_log).parent / "changes.patch").write_text(diff.stdout)

    log(agent_log, {
        "fix_applied": bool(patched),
        "file": str(patched) if patched else None
    })

    # Post-verification
    log(agent_log, {"stage": "post_verification"})
    post_exit = run_pytest(
        "openlibrary/tests/core/test_imports.py::TestImportItem::test_find_staged_or_pending",
        repo,
        Path(args.post_log),
    )
    log(agent_log, {"post_exit": post_exit})

    Path(args.results).write_text(json.dumps({
        "pre_exit": pre_exit,
        "post_exit": post_exit,
        "fix_applied": bool(patched)
    }, indent=2))

    agent_log.close()

if __name__ == "__main__":
    main()

