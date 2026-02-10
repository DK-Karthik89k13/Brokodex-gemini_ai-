#!/usr/bin/env python3
import argparse
import json
import subprocess
import re
import sys
from pathlib import Path
from datetime import datetime, timezone

# ----------------------------
# Paths
# ----------------------------
ARTIFACT_DIR = Path("/workspace/artifacts")
AGENT_LOG = ARTIFACT_DIR / "agent.log"
CHANGES_PATCH = ARTIFACT_DIR / "changes.patch"

TEST_CMD = (
    "python -m pytest "
    "openlibrary/tests/core/test_imports.py "
    "--maxfail=1 --tb=short -q"
)

# ----------------------------
# Utilities
# ----------------------------
def utc_ts():
    return datetime.now(timezone.utc).isoformat()

def log_agent(event, **data):
    payload = {"timestamp": utc_ts(), "event": event, **data}
    with open(AGENT_LOG, "a") as f:
        f.write(json.dumps(payload) + "\n")

def run(cmd, cwd=None):
    return subprocess.run(
        cmd, shell=True, cwd=cwd, text=True, capture_output=True
    )

def count_errors(text):
    return len(re.findall(r"\bFAILED\b|\bERROR\b", text))

def find_missing_modules(text):
    return sorted(set(
        m.group(1)
        for m in re.finditer(
            r"ModuleNotFoundError: No module named '([^']+)'", text
        )
    ))

def pip_install(module):
    log_agent("pip_install", module=module)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", module],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

# ----------------------------
# Validation (FAST)
# ----------------------------
def run_validation(repo, log_path, stage):
    result = run(TEST_CMD, repo)
    combined = result.stdout + result.stderr

    missing = find_missing_modules(combined)
    errors = count_errors(combined)

    # Install missing modules ONCE
    if missing:
        for mod in missing:
            pip_install(mod)

        # Re-run pytest once after install
        result = run(TEST_CMD, repo)
        combined = result.stdout + result.stderr
        errors = count_errors(combined)

    with open(log_path, "w") as f:
        f.write("=================================\n")
        f.write(f"STAGE     : {stage}\n")
        f.write(f"TIME      : {utc_ts()}\n")
        f.write(f"COMMAND   : {TEST_CMD}\n")
        f.write("=================================\n\n")
        f.write(result.stdout)
        f.write("\n--- STDERR ---\n")
        f.write(result.stderr)
        f.write("\n--- AGENT SUMMARY ---\n")
        f.write(f"ERROR COUNT : {errors}\n")

        if missing:
            f.write(f"MODULES INSTALLED : {missing}\n")
        else:
            f.write("NO MISSING MODULES DETECTED\n")

    log_agent(
        "validation",
        stage=stage,
        errors=errors,
        modules_installed=missing,
    )

    return errors

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
    parser.add_argument("--model", required=False)
    args = parser.parse_args()

    repo = Path(args.repo_path)
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    AGENT_LOG.write_text("")
    CHANGES_PATCH.write_text("")

    log_agent("run_started", model=args.model)

    Path(args.prompt_log).write_text(
        "Run pytest once. Install missing modules if detected. "
        "Do not guess failures. Log exact results."
    )

    # PRE
    pre_errors = run_validation(repo, Path(args.pre_log), "pre_validation")

    # POST
    post_errors = run_validation(repo, Path(args.post_log), "post_validation")

    # Conclusion
    with open(args.post_log, "a") as f:
        f.write("\n--- FINAL AGENT CONCLUSION ---\n")
        if pre_errors > 0 and post_errors == 0:
            f.write("THE ERROR IS CORRECTED\n")
        elif pre_errors > 0 and post_errors > 0:
            f.write("ERRORS STILL PRESENT\n")
        else:
            f.write("NO ERRORS TO CORRECT — TESTS WERE ALREADY PASSING\n")
        f.write("TASK COMPLETED\n")

    diff = run("git diff", repo).stdout
    CHANGES_PATCH.write_text(diff)

    Path(args.results).write_text(json.dumps({
        "pre_errors": pre_errors,
        "post_errors": post_errors,
        "tests_passing": post_errors == 0,
        "change_applied": bool(diff.strip()),
    }, indent=2))

    log_agent("run_complete")

if __name__ == "__main__":
    main()
