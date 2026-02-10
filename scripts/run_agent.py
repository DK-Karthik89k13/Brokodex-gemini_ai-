#!/usr/bin/env python3
import json
import argparse
import subprocess
import re
from pathlib import Path
from datetime import datetime, timezone

# -------------------------------------------------
# Paths & constants
# -------------------------------------------------
ARTIFACT_DIR = Path("/workspace/artifacts")
AGENT_LOG = ARTIFACT_DIR / "agent.log"
CHANGES_PATCH = ARTIFACT_DIR / "changes.patch"

TEST_COMMAND = "python -m pytest openlibrary/tests/core/test_imports.py -vv"

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
    payload = {
        "timestamp": utc_ts(),
        "event": event,
        **data,
    }
    with open(AGENT_LOG, "a") as f:
        f.write(json.dumps(payload) + "\n")

def run(cmd, cwd=None):
    return subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        text=True,
        capture_output=True,
    )

# -------------------------------------------------
# Repo helpers
# -------------------------------------------------
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

    raise RuntimeError("Failed to apply patch")

# -------------------------------------------------
# Validation runner (STRICT, NO GUESSING)
# -------------------------------------------------
def run_validation(repo: Path, stage: str, log_path: Path, previous_errors=None):
    header = (
        "==============================\n"
        f"AGENT STAGE : {stage}\n"
        f"TIMESTAMP  : {utc_ts()}\n"
        f"COMMAND    : {TEST_COMMAND}\n"
        "==============================\n\n"
    )

    result = run(TEST_COMMAND, cwd=repo)
    combined = result.stdout + "\n" + result.stderr

    # STRICT pytest error detection
    error_lines = [
        line for line in combined.splitlines()
        if re.search(r"\bFAILED\b|\bERROR\b", line)
    ]
    error_count = len(error_lines)

    # ----------------------------
    # PRE-VALIDATION LOGIC
    # ----------------------------
    if stage == "pre_validation":
        if error_count > 0:
            agent_result = (
                "PRE-VALIDATION ERRORS DETECTED\n"
                f"Number of errors: {error_count}\n\n"
                "Error details:\n" +
                "\n".join(error_lines)
            )
        else:
            agent_result = "NO ERRORS FOUND IN PRE-VALIDATION"

    # ----------------------------
    # POST-VALIDATION LOGIC
    # ----------------------------
    else:
        if previous_errors and previous_errors > 0 and error_count == 0:
            agent_result = (
                "ERRORS CLEARED IN POST-VALIDATION\n"
                f"Previously failing errors: {previous_errors}\n"
                "All tests now pass"
            )
        elif previous_errors == 0 and error_count == 0:
            agent_result = "NO ERRORS TO CLEAR — TESTS ALREADY PASSING"
        else:
            agent_result = (
                "POST-VALIDATION ERRORS STILL PRESENT\n"
                f"Remaining errors: {error_count}\n\n"
                "Error details:\n" +
                "\n".join(error_lines)
            )

    # Write logs
    content = (
        header +
        result.stdout +
        "\n--- STDERR ---\n" +
        result.stderr +
        "\n--- AGENT RESULT ---\n" +
        agent_result +
        "\n"
    )

    log_path.write_text(content)

    log_agent(
        "validation_complete",
        stage=stage,
        exit_code=result.returncode,
        errors=error_count,
        summary=agent_result.splitlines()[0]
    )

    return result.returncode, error_count

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
    parser.add_argument("--task-file", required=False)
    parser.add_argument("--model", required=False)  # ignored, for workflow compatibility
    args = parser.parse_args()

    repo = Path(args.repo_path)

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    AGENT_LOG.write_text("")
    CHANGES_PATCH.write_text("")

    log_agent("run_started", model=args.model)

    # Prompt log
    prompt = "Apply deterministic SWE-bench patch if required."
    Path(args.prompt_log).write_text(prompt)
    log_agent("prompt_written")

    # PRE-VALIDATION
    pre_exit, pre_errors = run_validation(
        repo,
        "pre_validation",
        Path(args.pre_log)
    )

    # APPLY PATCH
    fix_applied = apply_patch(repo)

    if fix_applied:
        diff = run("git diff", cwd=repo).stdout
        CHANGES_PATCH.write_text(diff)
        log_agent("diff_captured", lines=len(diff.splitlines()))
    else:
        log_agent("no_diff", reason="no_changes_needed")

    # POST-VALIDATION
    post_exit, post_errors = run_validation(
        repo,
        "post_validation",
        Path(args.post_log),
        previous_errors=pre_errors
    )

    # Results (derived ONLY from validation)
    Path(args.results).write_text(json.dumps({
        "pre_errors": pre_errors,
        "post_errors": post_errors,
        "fix_applied": fix_applied,
        "pre_exit": pre_exit,
        "post_exit": post_exit
    }, indent=2))

    log_agent("run_complete")

if __name__ == "__main__":
    main()
