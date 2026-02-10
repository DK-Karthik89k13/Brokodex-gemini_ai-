#!/usr/bin/env python3
import os
import sys
import json
import yaml
import subprocess
import argparse
from pathlib import Path
from datetime import datetime, timezone
import re

# ----------------------------
# Constants
# ----------------------------
AGENT_LOG = "/workspace/artifacts/agent.log"

# ----------------------------
# Helpers
# ----------------------------
def utc_ts():
    return datetime.now(timezone.utc).isoformat()

def agent_log(event, **data):
    payload = {
        "timestamp": utc_ts(),
        "event": event,
        **data
    }
    with open(AGENT_LOG, "a") as f:
        f.write(json.dumps(payload) + "\n")

def run(cmd, cwd=None):
    return subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        text=True,
        capture_output=True
    )

# ----------------------------
# Patch (deterministic SWE-bench)
# ----------------------------
PATCH_METHOD = """
    @classmethod
    def find_staged_or_pending(cls, identifiers, sources=None):
        if not identifiers:
            return cls.where("1=0")
        sources = sources or STAGED_SOURCES
        ia_ids = [f"{source}:{identifier}" for source in sources for identifier in identifiers]
        q = cls.where("ia_id IN $ia_ids", vars={"ia_ids": ia_ids})
        q = q.where("status IN ('staged', 'pending')")
        return q
""".rstrip()

# ----------------------------
# Repo helpers
# ----------------------------
def find_imports_file(repo: Path) -> Path:
    for p in repo.rglob("imports.py"):
        if "class ImportItem" in p.read_text():
            return p
    raise RuntimeError("ImportItem not found")

def apply_patch(repo: Path):
    target = find_imports_file(repo)
    code = target.read_text()

    if "find_staged_or_pending" in code:
        agent_log("patch_skipped", reason="already_present")
        return False

    lines = code.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("class ImportItem"):
            insert_at = i + 1
            while insert_at < len(lines) and lines[insert_at].strip():
                insert_at += 1
            lines.insert(insert_at, PATCH_METHOD)
            target.write_text("\n".join(lines) + "\n")
            agent_log("patch_applied", file=str(target))
            return True

    raise RuntimeError("Failed to apply patch")

# ----------------------------
# Pytest runner
# ----------------------------
def run_validation(repo, test_path, log_path, stage):
    result = run(f"python -m pytest {test_path} -vv", cwd=repo)

    Path(log_path).write_text(
        result.stdout + "\n\n" + result.stderr
    )

    errors = len(re.findall(r"FAILED|ERROR", result.stdout + result.stderr))

    agent_log(
        f"{stage}_validation",
        exit_code=result.returncode,
        errors=errors
    )

    return result.returncode, errors

# ----------------------------
# Main
# ----------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--pre-log", required=True)
    parser.add_argument("--post-log", required=True)
    parser.add_argument("--prompt-log", required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument("--task-file", required=False)
    args = parser.parse_args()

    repo = Path(args.repo_path)
    Path(AGENT_LOG).write_text("")  # reset

    # Prompt log (deterministic agent)
    system_prompt = "Apply SWE-bench deterministic patch for ImportItem.find_staged_or_pending"
    Path(args.prompt_log).write_text(system_prompt)
    agent_log("prompt_written")

    # Pre-validation
    pre_exit, pre_errors = run_validation(
        repo,
        "openlibrary/tests/core/test_imports.py",
        args.pre_log,
        "pre"
    )

    # Apply patch
    patch_applied = apply_patch(repo)

    # Post-validation
    post_exit, post_errors = run_validation(
        repo,
        "openlibrary/tests/core/test_imports.py",
        args.post_log,
        "post"
    )

    # Capture diff ONLY if agent acted
    diff_path = "/workspace/artifacts/changes.patch"
    if patch_applied:
        diff = run("git diff", cwd=repo).stdout
        Path(diff_path).write_text(diff)
        agent_log("diff_captured", lines=len(diff.splitlines()))
    else:
        Path(diff_path).write_text("")
        agent_log("diff_empty")

    # Results
    Path(args.results).write_text(json.dumps({
        "pre_exit": pre_exit,
        "post_exit": post_exit,
        "pre_errors": pre_errors,
        "post_errors": post_errors,
        "fix_applied": patch_applied
    }, indent=2))

    agent_log("run_complete")

if __name__ == "__main__":
    main()
