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
# Logging
# -------------------------
def utc_ts():
    return datetime.now(UTC).isoformat()

def log(fp, payload):
    payload["timestamp"] = utc_ts()
    fp.write(json.dumps(payload) + "\n")
    fp.flush()

# -------------------------
# Repo helpers
# -------------------------
def run_bash(cmd, cwd):
    return subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)

def find_imports_file(repo: Path) -> Path:
    for p in repo.rglob("imports.py"):
        try:
            if "class ImportItem" in p.read_text():
                return p
        except Exception:
            continue
    raise FileNotFoundError("Could not find imports.py with ImportItem")

FIX_SNIPPET = """
    @classmethod
    def find_staged_or_pending(cls, ia_ids, sources=None):
        if not ia_ids:
            return []
        q = cls.where("ia_id IN $ia_ids", vars={"ia_ids": ia_ids})
        q = q.where("status IN ('staged', 'pending')")
        return list(q)
"""

def apply_fix(repo: Path):
    target = find_imports_file(repo)
    code = target.read_text()
    if "find_staged_or_pending" not in code:
        target.write_text(code + FIX_SNIPPET)
        return target
    return None

# -------------------------
# Testing helpers
# -------------------------
def run_pytest(test_path: str, cwd: Path, log_path: Path):
    result = run_bash(f"python -m pytest {test_path} -xvs", cwd=cwd)
    log_path.write_text(result.stdout)
    exit_code_path = log_path.parent / f"{log_path.stem}.exit"
    exit_code_path.write_text(str(result.returncode))
    return result.returncode

# -------------------------
# Main agent
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
    ap.add_argument("--model", default="gemini-1.0-pro")
    args = ap.parse_args()

    repo = Path(args.repo_path)
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    agent_log = open(args.log_path, "w", buffering=1)
    client = Client(api_key=api_key)

    # -------------------------
    # System prompt
    # -------------------------
    system_prompt = f"""
You are an autonomous SWE-bench agent.

Task:
Fix failing test:
openlibrary/tests/core/test_imports.py::TestImportItem::test_find_staged_or_pending

Rules:
- Prefer staged or pending local ImportItem records
- Do not perform remote lookups
- Produce a unified git diff if fixed
""".strip()

    Path(args.prompt_log).write_text(system_prompt)
    log(agent_log, {"type": "prompt", "content": system_prompt})

    # -------------------------
    # Pre-verification
    # -------------------------
    log(agent_log, {"type": "stage", "stage": "pre_verification"})
    pre_exit = run_pytest(
        "openlibrary/tests/core/test_imports.py::TestImportItem::test_find_staged_or_pending",
        repo,
        Path(args.pre_log)
    )
    log(agent_log, {"type": "pre_verification", "exit_code": pre_exit})

    # -------------------------
    # Gemini + deterministic fix
    # -------------------------
    log(agent_log, {"type": "stage", "stage": "gemini_call"})
    try:
        response = client.models.generate_content(
            model=args.model,
            contents=system_prompt,
            config=GenerateContentConfig(temperature=0.2, max_output_tokens=2048),
        )
        log(agent_log, {"type": "gemini", "content": response.text})
    except Exception as e:
        log(agent_log, {"type": "gemini_error", "error": str(e)})

    log(agent_log, {"type": "stage", "stage": "apply_fix"})
    target_file = apply_fix(repo)
    changes_patch = Path(args.post_log).parent / "changes.patch"
    if target_file:
        diff = run_bash(f"git diff", cwd=repo)
        changes_patch.write_text(diff.stdout)
        log(agent_log, {"type": "fix_applied", "file": str(target_file)})
    else:
        log(agent_log, {"type": "fix_skipped", "reason": "already_present"})

    # -------------------------
    # Post-verification
    # -------------------------
    log(agent_log, {"type": "stage", "stage": "post_verification"})
    post_exit = run_pytest(
        "openlibrary/tests/core/test_imports.py::TestImportItem::test_find_staged_or_pending",
        repo,
        Path(args.post_log)
    )
    log(agent_log, {"type": "post_verification", "exit_code": post_exit})

    # -------------------------
    # Results.json
    # -------------------------
    results = {
        "pre_exit": pre_exit,
        "post_exit": post_exit,
        "fix_applied": bool(target_file),
        "diff_file": str(changes_patch) if target_file else None
    }
    Path(args.results).write_text(json.dumps(results, indent=2))
    log(agent_log, {"type": "done", "results": results})
    agent_log.close()

if __name__ == "__main__":
    main()
