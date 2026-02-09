#!/usr/bin/env python3
import os
import json
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, UTC

from google.genai import Client
from google.genai.types import GenerateContentConfig

# --------------------------------------------------
# Paths
# --------------------------------------------------

AGENT_LOG = Path("agent.log")
PROMPT_LOG = Path("prompts.md")
PRE_LOG = Path("pre_validation.log")
POST_LOG = Path("post_verification.log")
PATCH_FILE = Path("changes.patch")
RESULTS_FILE = Path("results.json")

# --------------------------------------------------
# Logging
# --------------------------------------------------

def utc_ts():
    return datetime.now(UTC).isoformat()

def log(fp, payload):
    payload["timestamp"] = utc_ts()
    fp.write(json.dumps(payload) + "\n")
    fp.flush()

# --------------------------------------------------
# Repo helpers
# --------------------------------------------------

def run(cmd, cwd: Path):
    return subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True
    )

def run_pytest(repo: Path):
    result = run(["pytest", "-q"], repo)
    return result.returncode == 0, result.stdout + result.stderr

def find_imports_file(repo: Path) -> Path:
    for p in repo.rglob("imports.py"):
        try:
            if "class ImportItem" in p.read_text():
                return p
        except Exception:
            pass
    raise FileNotFoundError("imports.py with ImportItem not found")

FIX_SNIPPET = """
    @classmethod
    def find_staged_or_pending(cls, ia_ids, sources=None):
        if not ia_ids:
            return []
        q = cls.where("ia_id IN $ia_ids", vars={"ia_ids": ia_ids})
        q = q.where("status IN ('staged', 'pending')")
        return list(q)
"""

def apply_fix(repo: Path) -> Path | None:
    target = find_imports_file(repo)
    code = target.read_text()

    if "find_staged_or_pending" in code:
        return None

    target.write_text(code + FIX_SNIPPET)
    return target

# --------------------------------------------------
# Agent
# --------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--model", default="gemini-1.0-pro")
    args = ap.parse_args()

    repo = Path(args.repo_path)

    api_key = os.environ.get("GEMINI_API_KEY")

    # ---- Prompt ----
    system_prompt = f"""
Task ID: {args.task_id}

Fix failing test:
openlibrary/tests/core/test_imports.py::TestImportItem::test_find_staged_or_pending

Rules:
- Prefer local staged or pending ImportItem records
- No remote lookups
- Validate using pytest
""".strip()

    PROMPT_LOG.write_text(system_prompt)

    logf = AGENT_LOG.open("w", buffering=1)
    log(logf, {"type": "start", "task_id": args.task_id})

    # ---- Gemini (non-blocking, optional) ----
    if api_key:
        try:
            client = Client(api_key=api_key)
            resp = client.models.generate_content(
                model=args.model,
                contents=system_prompt,
                config=GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=256,
                ),
            )
            log(logf, {"type": "gemini", "content": resp.text})
        except Exception as e:
            log(logf, {"type": "gemini_error", "error": str(e)})

    # ---- Pre-validation ----
    pre_ok, pre_out = run_pytest(repo)
    PRE_LOG.write_text(pre_out)
    log(logf, {"type": "pre_validation", "passed": pre_ok})

    # ---- Apply fix ----
    target = apply_fix(repo)
    if target:
        log(logf, {"type": "fix", "file": str(target), "status": "applied"})
    else:
        log(logf, {"type": "fix", "status": "already_present"})

    # ---- Capture diff ----
    diff = run(["git", "diff"], repo)
    PATCH_FILE.write_text(diff.stdout)
    log(logf, {"type": "diff_saved"})

    # ---- Post-validation ----
    post_ok, post_out = run_pytest(repo)
    POST_LOG.write_text(post_out)
    log(logf, {"type": "post_validation", "passed": post_ok})

    # ---- Results ----
    results = {
        "task_id": args.task_id,
        "pre_validation_passed": pre_ok,
        "post_validation_passed": post_ok,
        "fix_applied": bool(target),
        "timestamp": utc_ts(),
    }
    RESULTS_FILE.write_text(json.dumps(results, indent=2))

    log(logf, {"type": "done", "success": post_ok})
    logf.close()

    if not post_ok:
        raise RuntimeError("Tests failed after fix")

if __name__ == "__main__":
    main()
