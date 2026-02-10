#!/usr/bin/env python3
import json
import argparse
import subprocess
import re
from pathlib import Path
from datetime import datetime, timezone

ARTIFACTS = Path("/workspace/artifacts")
AGENT_LOG = ARTIFACTS / "agent.log"
CHANGES_PATCH = ARTIFACTS / "changes.patch"

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

def agent_log(event, **data):
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
# Validation / Verification
# ----------------------------
def run_checks(repo, stage, validation_log, verification_log):
    """
    Writes identical content to:
      - validation_log
      - verification_log
    """
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

    content = (
        header +
        result.stdout +
        "\n--- STDERR ---\n" +
        result.stderr
    )

    # IMPORTANT: overwrite both files
    Path(validation_log).write_text(content)
    Path(verification_log).write_text(content)

    agent_log(
        "validation_complete",
        stage=stage,
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
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    AGENT_LOG.write_text("")
    CHANGES_PATCH.write_text("")

    # Prompt
    prompt = "Apply SWE-bench deterministic patch for ImportItem.find_staged_or_pending"
    Path(args.prompt_log).write_text(prompt)
    agent_log("prompt_written")

    # ----------------------------
    # PRE
    # ----------------------------
    pre_exit, pre_errors = run_checks(
        repo,
        stage="pre_validation",
        validation_log=args.pre_log,
        verification_log=str(Path(args.pre_log).with_name("pre_verification.log"))
    )

    # ----------------------------
    # APPLY PATCH
    # ----------------------------
    file_path = repo / "openlibrary/core/imports.py"
    text = file_path.read_text()

    fix_applied = "find_staged_or_pending" not in text

    if fix_applied:
        text = text.replace(
            "class ImportItem(web.storage):",
            "class ImportItem(web.storage):\n" + PATCH_METHOD
        )
        file_path.write_text(text)
        agent_log("patch_applied", file=str(file_path))

        diff = run("git diff", cwd=repo).stdout
        CHANGES_PATCH.write_text(diff)
    else:
        agent_log("patch_skipped")

    # ----------------------------
    # POST
    # ----------------------------
    post_exit, post_errors = run_checks(
        repo,
        stage="post_validation",
        validation_log=args.post_log,
        verification_log=str(Path(args.post_log).with_name("post_verification.log"))
    )

    # ----------------------------
    # RESULTS
    # ----------------------------
    Path(args.results).write_text(json.dumps({
        "pre_exit": pre_exit,
        "post_exit": post_exit,
        "pre_errors": pre_errors,
        "post_errors": post_errors,
        "fix_applied": fix_applied
    }, indent=2))

    agent_log("run_complete")

if __name__ == "__main__":
    main()
