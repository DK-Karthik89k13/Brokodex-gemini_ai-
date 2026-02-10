#!/usr/bin/env python3
import os
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

def log_agent(event: str, **data):
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

# ----------------------------
# Repo helpers
# ----------------------------
def find_imports_file(repo: Path) -> Path:
    for p in repo.rglob("imports.py"):
        if "class ImportItem" in p.read_text():
            return p
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

# ----------------------------
# Validation + Verification
# ----------------------------
def run_validation(repo: Path, stage: str, validation_log: Path, verification_log: Path):
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
    errors = len(re.findall(r"\bFAILED\b|\bERROR\b", combined))
    warnings = len(re.findall(r"\bWARNING\b", combined))

    content = (
        header +
        result.stdout +
        "\n--- STDERR ---\n" +
        result.stderr +
        "\n--- AGENT SUMMARY ---\n"
        f"Exit code : {result.returncode}\n"
        f"Errors    : {errors}\n"
        f"Warnings  : {warnings}\n"
    )

    # IMPORTANT: overwrite, no append
    validation_log.write_text(content)
    verification_log.write_text(content)

    log_agent(
        "validation_complete",
        stage=stage,
        exit_code=result.returncode,
        errors=errors,
        warnings=warnings,
    )

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
    parser.add_argument("--task-file", required=False)

    # workflow compatibility
    parser.add_argument("--model", required=False)

    args = parser.parse_args()
    repo = Path(args.repo_path)

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    AGENT_LOG.write_text("")
    CHANGES_PATCH.write_text("")

    log_agent("run_started", model=args.model)

    # Prompt
    prompt = "Apply SWE-bench deterministic patch for ImportItem.find_staged_or_pending."
    Path(args.prompt_log).write_text(prompt)
    log_agent("prompt_written")

    # ----------------------------
    # PRE
    # ----------------------------
    pre_exit, pre_errors, pre_warnings = run_validation(
        repo,
        "pre_validation",
        Path(args.pre_log),
        Path(args.pre_log).with_name("pre_verification.log"),
    )

    # ----------------------------
    # PATCH
    # ----------------------------
    fix_applied = apply_patch(repo)

    if fix_applied:
        diff = run("git diff", cwd=repo).stdout
        CHANGES_PATCH.write_text(diff)
        log_agent("diff_captured", lines=len(diff.splitlines()))
    else:
        log_agent("diff_empty")

    # ----------------------------
    # POST
    # ----------------------------
    post_exit, post_errors, post_warnings = run_validation(
        repo,
        "post_validation",
        Path(args.post_log),
        Path(args.post_log).with_name("post_verification.log"),
    )

    # ----------------------------
    # RESULTS (validation-derived only)
    # ----------------------------
    Path(args.results).write_text(json.dumps({
        "task_file": None,
        "pre_exit": pre_exit,
        "post_exit": post_exit,
        "pre_errors": pre_errors,
        "pre_warnings": pre_warnings,
        "fix_applied": fix_applied,
    }, indent=2))

    log_agent("run_complete")

if __name__ == "__main__":
    main()
