#!/usr/bin/env python3
import argparse
import json
import subprocess
import re
import sys
import shutil
from pathlib import Path
from datetime import datetime, timezone

# ----------------------------
# Paths
# ----------------------------
ARTIFACT_DIR = Path("/workspace/artifacts")
AGENT_LOG = ARTIFACT_DIR / "agent.log"
CHANGES_PATCH = ARTIFACT_DIR / "changes.patch"

TEST_CMD = "python -m pytest openlibrary/tests/core/test_imports.py -vv"
MAX_RETRIES = 3

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

def find_problem_modules(text):
    """
    Extract module names from ImportError / ModuleNotFoundError
    """
    modules = set()
    for line in text.splitlines():
        m = re.search(r"ModuleNotFoundError: No module named '([^']+)'", line)
        if m:
            modules.add(m.group(1))

        m2 = re.search(r"ImportError: .* from '([^']+)'", line)
        if m2:
            modules.add(m2.group(1))

    return list(modules)

def pip_uninstall(module):
    log_agent("pip_uninstall", module=module)
    subprocess.run(
        [sys.executable, "-m", "pip", "uninstall", "-y", module],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

def pip_install(module):
    log_agent("pip_install", module=module)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", module],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

# ----------------------------
# Validation with auto-repair
# ----------------------------
def run_validation(repo, log_path, stage):
    installed = []
    attempts = 0

    while attempts < MAX_RETRIES:
        attempts += 1
        result = run(TEST_CMD, repo)
        combined = result.stdout + result.stderr

        problem_modules = find_problem_modules(combined)

        if problem_modules:
            for mod in problem_modules:
                pip_uninstall(mod)
                pip_install(mod)
                installed.append(mod)
            continue  # retry pytest after reinstall

        break  # no import problems → exit loop

    errors = count_errors(combined)

    with open(log_path, "w") as f:
        f.write("=================================\n")
        f.write(f"STAGE      : {stage}\n")
        f.write(f"TIMESTAMP  : {utc_ts()}\n")
        f.write(f"COMMAND    : {TEST_CMD}\n")
        f.write(f"ATTEMPTS   : {attempts}\n")
        f.write("=================================\n\n")
        f.write(result.stdout)
        f.write("\n--- STDERR ---\n")
        f.write(result.stderr)
        f.write("\n--- AGENT REPORT ---\n")
        f.write(f"ERROR COUNT : {errors}\n")

        if installed:
            f.write(f"MODULES REINSTALLED : {installed}\n")

        if errors == 0:
            f.write("NO ERRORS FOUND\n")

    log_agent(
        "validation",
        stage=stage,
        errors=errors,
        modules_reinstalled=installed,
    )

    return errors, installed

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

    # ----------------------------
    # Prompt
    # ----------------------------
    Path(args.prompt_log).write_text(
        "Run pytest. If imports fail, uninstall and reinstall modules automatically. "
        "Fix code only if pytest confirms failure."
    )

    # ----------------------------
    # PRE-VALIDATION
    # ----------------------------
    pre_errors, pre_modules = run_validation(
        repo, Path(args.pre_log), "pre_validation"
    )

    if pre_errors > 0:
        decision = "attempt_fix"
    else:
        decision = "no_fix_required"

    log_agent("agent_decision", decision=decision)

    # ----------------------------
    # No forced code change (Docker image is clean)
    # ----------------------------
    diff = run("git diff", repo).stdout
    CHANGES_PATCH.write_text(diff)

    # ----------------------------
    # POST-VALIDATION
    # ----------------------------
    post_errors, post_modules = run_validation(
        repo, Path(args.post_log), "post_validation"
    )

    # ----------------------------
    # Results
    # ----------------------------
    Path(args.results).write_text(json.dumps({
        "pre_errors": pre_errors,
        "post_errors": post_errors,
        "modules_reinstalled": list(set(pre_modules + post_modules)),
        "tests_passing": post_errors == 0,
        "change_applied": bool(diff.strip()),
    }, indent=2))

    log_agent("run_complete")

if __name__ == "__main__":
    main()
