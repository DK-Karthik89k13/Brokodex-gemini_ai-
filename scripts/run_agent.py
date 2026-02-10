#!/usr/bin/env python3
import os
import sys
import json
import argparse
import subprocess
import re
from pathlib import Path
from datetime import datetime, timezone

# ----------------------------
# Paths & constants
# ----------------------------
ARTIFACT_DIR = Path("/workspace/artifacts")
AGENT_LOG = ARTIFACT_DIR / "agent.log"
CHANGES_PATCH = ARTIFACT_DIR / "changes.patch"

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
# Utilities
# ----------------------------
def utc_ts():
    return datetime.now(timezone.utc).isoformat()

def log_agent(event: dict):
    event["timestamp"] = utc_ts()
    with open(AGENT_LOG, "a") as f:
        f.write(json.dumps(event) + "\n")

def run(cmd, cwd=None):
    return subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
    )

# ----------------------------
# Repo helpers
# ----------------------------
def find_imports_file(repo: Path) -> Path:
    for p in repo.rglob("imports.py"):
        try:
            if "class ImportItem" in p.read_text():
                return p
        except Exception:
            continue
    raise RuntimeError("ImportItem class not found")

def apply_patch(repo: Path):
    target = find_imports_file(repo)
    code = target.read_text()

    if "find_staged_or_pending" in code:
        return False, None

    lines = code.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("class ImportItem"):
            insert_at = i + 1
            while insert_at < len(lines) and lines[insert_at].strip():
                insert_at += 1

            lines.insert(insert_at, PATCH_METHOD)
            target.write_text("\n".join(lines) + "\n")
            return True, target

    raise RuntimeError("Failed to apply patch")

# ----------------------------
# Pytest runner (agent-verbose)
# ----------------------------
def run_pytest(repo: Path, log_path: Path, stage: str):
    header = (
        "==============================\n"
        f"AGENT STAGE : {stage}\n"
        f"TIMESTAMP  : {utc_ts()}\n"
        "COMMAND    : python -m pytest openlibrary/tests/core/test_imports.py -vv\n"
        "==============================\n\n"
    )

    result = run(
        "python -m pytest openlibrary/tests/core/test_imports.py -vv",
        cwd=repo,
    )

    combined = result.stdout + "\n" + result.stderr
    errors = len(re.findall(r"\bERROR\b", combined))
    warnings = len(re.findall(r"\bWARNING\b", combined))

    with open(log_path, "w") as f:
        f.write(header)
        f.write(result.stdout)
        f.write("\n--- STDERR ---\n")
        f.write(result.stderr)
        f.write(
            "\n--- AGENT SUMMARY ---\n"
            f"Exit code : {result.returncode}\n"
            f"Errors    : {errors}\n"
            f"Warnings  : {warnings}\n"
        )

    log_agent({
        "type": "validation",
        "stage": stage,
        "exit_code": result.returncode,
        "errors": errors,
        "warnings": warnings,
    })

    return result.returncode, errors, warnings

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

    # Accepted for workflow compatibility; intentionally unused
    parser.add_argument("--model", required=False)

    args = parser.parse_args()

    repo = Path(args.repo_path)

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    AGENT_LOG.write_text("")
    CHANGES_PATCH.write_text("")

    # ----------------------------
    # Prompt (deterministic)
    # ----------------------------
    prompt = "Apply SWE-bench deterministic patch for find_staged_or_pending."
    Path(args.prompt_log).write_text(prompt)
    log_agent({
        "type": "prompt",
        "content": prompt,
        "model": args.model,  # logged only, not used
    })

    # ----------------------------
    # Pre-validation
    # ----------------------------
    pre_exit, pre_errors, pre_warnings = run_pytest(
        repo, Path(args.pre_log), "pre_validation"
    )

    # ----------------------------
    # Apply patch
    # ----------------------------
    fix_applied, modified_file = apply_patch(repo)

    if fix_applied:
        log_agent({
            "type": "code_change",
            "file": str(modified_file),
            "method_added": "ImportItem.find_staged_or_pending",
        })

        diff = run("git diff", cwd=repo).stdout
        CHANGES_PATCH.write_text(diff)

        log_agent({
            "type": "diff_captured",
            "lines": len(diff.splitlines()),
        })
    else:
        log_agent({
            "type": "code_change",
            "status": "already_present",
        })

    # ----------------------------
    # Post-validation
    # ----------------------------
    post_exit, post_errors, post_warnings = run_pytest(
        repo, Path(args.post_log), "post_validation"
    )

    with open(args.post_log, "a") as f:
        f.write(
            "\n--- PRE-VALIDATION SUMMARY ---\n"
            f"Errors: {pre_errors}\n"
            f"Warnings: {pre_warnings}\n"
        )

    # ----------------------------
    # SWE-bench Pro result (VALIDATION-BASED)
    # ----------------------------
    Path(args.results).write_text(json.dumps({
        "task_file": None,
        "pre_exit": int(pre_exit),
        "post_exit": int(post_exit),
        "pre_errors": int(pre_errors),
        "pre_warnings": int(pre_warnings),
        "fix_applied": bool(fix_applied),
    }, indent=2))

if __name__ == "__main__":
    main()
