#!/usr/bin/env python3
import json
import argparse
import subprocess
import re
from pathlib import Path
from datetime import datetime, timezone

# -------------------------------------------------
# Paths
# -------------------------------------------------
ARTIFACT_DIR = Path("/workspace/artifacts")
AGENT_LOG = ARTIFACT_DIR / "agent.log"
CHANGES_PATCH = ARTIFACT_DIR / "changes.patch"

# -------------------------------------------------
# SWE-bench patch
# -------------------------------------------------
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

# -------------------------------------------------
# Utilities
# -------------------------------------------------
def utc_ts():
    return datetime.now(timezone.utc).isoformat()

def log_agent(event, **data):
    with open(AGENT_LOG, "a") as f:
        f.write(json.dumps({
            "timestamp": utc_ts(),
            "event": event,
            **data
        }) + "\n")

def run(cmd, cwd=None):
    return subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        text=True,
        capture_output=True
    )

# -------------------------------------------------
# Repo helpers
# -------------------------------------------------
def find_imports_file(repo: Path) -> Path:
    for p in repo.rglob("imports.py"):
        if "class ImportItem" in p.read_text():
            return p
    raise RuntimeError("ImportItem not found")

def apply_patch(repo: Path):
    target = find_imports_file(repo)
    code = target.read_text()

    if "find_staged_or_pending" in code:
        log_agent("patch_skipped", reason="already_present")
        return False

    lines = code.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("class ImportItem"):
            insert_at = i + 1
            while insert_at < len(lines) and lines[insert_at].strip():
                insert_at += 1
            lines.insert(insert_at, PATCH_METHOD)
            target.write_text("\n".join(lines) + "\n")
            log_agent("patch_applied", file=str(target))
            return True

    raise RuntimeError("Patch insertion failed")

# -------------------------------------------------
# Validation (REAL pytest results only)
# -------------------------------------------------
def run_validation(repo, stage, log_path, previous_errors=None):
    result = run(
        "python -m pytest openlibrary/tests/core/test_imports.py -vv",
        cwd=repo
    )

    combined = result.stdout + "\n" + result.stderr

    error_lines = [
        l for l in combined.splitlines()
        if "FAILED" in l or "ERROR" in l
    ]
    error_count = len(error_lines)

    # -----------------------------
    # Agent interpretation
    # -----------------------------
    if stage == "pre_validation":
        if error_count > 0:
            agent_report = (
                "PRE-VALIDATION ERRORS DETECTED\n"
                f"Number of errors: {error_count}\n\n"
                "Error details:\n" +
                "\n".join(error_lines)
            )
        else:
            agent_report = (
                "NO ERRORS FOUND IN PRE-VALIDATION\n"
                "Number of errors: 0"
            )

    else:  # post_validation
        if previous_errors > 0 and error_count == 0:
            agent_report = (
                "ERRORS CLEARED IN POST-VALIDATION\n"
                f"Errors before: {previous_errors}\n"
                "Errors now   : 0\n"
                "All tests passed"
            )
        elif previous_errors == 0 and error_count == 0:
            agent_report = (
                "NO ERRORS TO CLEAR — TESTS ALREADY PASSING\n"
                "Errors before: 0\n"
                "Errors now   : 0"
            )
        else:
            agent_report = (
                "POST-VALIDATION ERRORS STILL PRESENT\n"
                f"Errors before: {previous_errors}\n"
                f"Errors now   : {error_count}\n\n"
                "Remaining error details:\n" +
                "\n".join(error_lines)
            )

    log_path.write_text(
        result.stdout +
        "\n--- STDERR ---\n" +
        result.stderr +
        "\n--- AGENT REPORT ---\n" +
        agent_report +
        "\n"
    )

    log_agent(
        "validation_complete",
        stage=stage,
        exit_code=result.returncode,
        errors=error_count
    )

    return error_count

# -------------------------------------------------
# Main
# -------------------------------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--pre-log", required=True)
    parser.add_argument("--post-log", required=True)
    parser.add_argument("--prompt-log", required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument("--model", required=False)

    args = parser.parse_args()
    repo = Path(args.repo_path)

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    AGENT_LOG.write_text("")
    CHANGES_PATCH.write_text("")

    log_agent("run_started")

    # Prompt (deterministic)
    Path(args.prompt_log).write_text(
        "Apply SWE-bench deterministic patch for ImportItem.find_staged_or_pending."
    )

    # -----------------------------
    # Pre-validation
    # -----------------------------
    pre_errors = run_validation(
        repo,
        "pre_validation",
        Path(args.pre_log)
    )

    # -----------------------------
    # Apply patch
    # -----------------------------
    fix_applied = apply_patch(repo)

    if fix_applied:
        diff = run("git diff", cwd=repo).stdout
        CHANGES_PATCH.write_text(diff)

    # -----------------------------
    # Post-validation
    # -----------------------------
    post_errors = run_validation(
        repo,
        "post_validation",
        Path(args.post_log),
        previous_errors=pre_errors
    )

    # -----------------------------
    # Results (truthful)
    # -----------------------------
    Path(args.results).write_text(json.dumps({
        "pre_errors": pre_errors,
        "post_errors": post_errors,
        "fix_applied": fix_applied
    }, indent=2))

    log_agent("run_complete")

if __name__ == "__main__":
    main()
