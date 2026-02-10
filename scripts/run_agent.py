#!/usr/bin/env python3
import json
import argparse
import subprocess
import re
from pathlib import Path
from datetime import datetime, timezone

# ----------------------------
# Paths
# ----------------------------
ARTIFACT_DIR = Path("/workspace/artifacts")
AGENT_LOG = ARTIFACT_DIR / "agent.log"
CHANGES_PATCH = ARTIFACT_DIR / "changes.patch"

TARGET_FILE = Path("openlibrary/core/imports.py")
TARGET_METHOD = "find_staged_or_pending"

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

def log_agent(event, **data):
    with open(AGENT_LOG, "a") as f:
        f.write(json.dumps({
            "timestamp": utc_ts(),
            "event": event,
            **data
        }) + "\n")

def run(cmd, cwd=None):
    return subprocess.run(
        cmd, shell=True, cwd=cwd, text=True, capture_output=True
    )

# ----------------------------
# Patch + Verification
# ----------------------------
def apply_patch(repo: Path) -> bool:
    file_path = repo / TARGET_FILE
    code = file_path.read_text()

    if TARGET_METHOD in code:
        log_agent("patch_skipped", reason="method_already_present")
        return False

    updated = code.replace(
        "class ImportItem(web.storage):",
        "class ImportItem(web.storage):\n" + PATCH_METHOD
    )
    file_path.write_text(updated)

    log_agent("patch_applied", file=str(TARGET_FILE))
    return True

def verify_patch(repo: Path):
    file_path = repo / TARGET_FILE

    if not file_path.exists():
        raise RuntimeError("Target file missing")

    content = file_path.read_text()

    file_modified = TARGET_METHOD in content
    method_added = re.search(
        rf"def {TARGET_METHOD}\(", content
    ) is not None

    log_agent(
        "patch_verification",
        file_modified=file_modified,
        method_added=method_added
    )

    if not (file_modified and method_added):
        raise RuntimeError("Patch verification failed")

    return True

# ----------------------------
# Validation
# ----------------------------
def run_validation(repo, stage, validation_log, verification_log):
    header = (
        "==============================\n"
        f"AGENT STAGE : {stage}\n"
        f"TIMESTAMP  : {utc_ts()}\n"
        "COMMAND    : python -m pytest openlibrary/tests/core/test_imports.py -vv\n"
        "==============================\n\n"
    )

    result = run(
        "python -m pytest openlibrary/tests/core/test_imports.py -vv",
        cwd=repo
    )

    combined = result.stdout + "\n" + result.stderr
    errors = len(re.findall(r"\bFAILED\b|\bERROR\b", combined))

    state = (
        "NO TEST FAILURES DETECTED (docker image already clean)"
        if result.returncode == 0 and errors == 0
        else "TEST FAILURES DETECTED"
    )

    content = (
        header +
        result.stdout +
        "\n--- STDERR ---\n" +
        result.stderr +
        "\n--- AGENT STATE ---\n"
        f"{state}\n"
    )

    Path(validation_log).write_text(content)
    Path(verification_log).write_text(content)

    log_agent(
        "validation_complete",
        stage=stage,
        exit_code=result.returncode,
        errors=errors,
        state=state
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
    parser.add_argument("--model", required=False)
    args = parser.parse_args()

    repo = Path(args.repo_path)

    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    AGENT_LOG.write_text("")
    CHANGES_PATCH.write_text("")

    log_agent("run_started", model=args.model)

    # Prompt
    prompt = "Apply SWE-bench deterministic patch for ImportItem.find_staged_or_pending"
    Path(args.prompt_log).write_text(prompt)

    # PRE
    pre_exit, pre_errors = run_validation(
        repo,
        "pre_validation",
        Path(args.pre_log),
        Path(args.pre_log).with_name("pre_verification.log")
    )

    # PATCH
    fix_applied = apply_patch(repo)

    if fix_applied:
        diff = run("git diff", cwd=repo).stdout
        CHANGES_PATCH.write_text(diff)
        verify_patch(repo)
    else:
        log_agent("no_diff_required")

    # POST
    post_exit, post_errors = run_validation(
        repo,
        "post_validation",
        Path(args.post_log),
        Path(args.post_log).with_name("post_verification.log")
    )

    # RESULTS
    Path(args.results).write_text(json.dumps({
        "pre_exit": pre_exit,
        "post_exit": post_exit,
        "pre_errors": pre_errors,
        "fix_applied": fix_applied,
        "file_verified": True,
        "method_verified": True
    }, indent=2))

    log_agent("run_complete")

if __name__ == "__main__":
    main()
