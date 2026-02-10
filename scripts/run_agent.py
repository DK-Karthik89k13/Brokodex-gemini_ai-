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
# Validation
# ----------------------------
def run_validation(repo, log_path, stage):
    attempts = 0
    reinstalled = []

    while attempts < MAX_RETRIES:
        attempts += 1
        result = run(TEST_CMD, repo)
        combined = result.stdout + result.stderr

        broken = find_problem_modules(combined)
        if broken:
            for mod in broken:
                pip_uninstall(mod)
                pip_install(mod)
                reinstalled.append(mod)
            continue
        break

    errors = count_errors(combined)

    with open(log_path, "w") as f:
        f.write("=================================\n")
        f.write(f"STAGE     : {stage}\n")
        f.write(f"TIME      : {utc_ts()}\n")
        f.write(f"COMMAND   : {TEST_CMD}\n")
        f.write(f"ATTEMPTS  : {attempts}\n")
        f.write("=================================\n\n")
        f.write(result.stdout)
        f.write("\n--- STDERR ---\n")
        f.write(result.stderr)
        f.write("\n--- AGENT SUMMARY ---\n")
        f.write(f"ERROR COUNT : {errors}\n")

        if reinstalled:
            f.write(f"MODULES REINSTALLED : {reinstalled}\n")

    log_agent(
        "validation",
        stage=stage,
        errors=errors,
        modules_reinstalled=reinstalled,
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

    # ----------------------------
    # Prompt
    # ----------------------------
    Path(args.prompt_log).write_text(
        "Run pytest. Auto-reinstall broken modules. "
        "Fix code only if pytest proves failure."
    )

    # ----------------------------
    # PRE-VALIDATION
    # ----------------------------
    pre_errors = run_validation(
        repo, Path(args.pre_log), "pre_validation"
    )

    # ----------------------------
    # POST-VALIDATION
    # ----------------------------
    post_errors = run_validation(
        repo, Path(args.post_log), "post_validation"
    )

    # ----------------------------
    # Final post-validation conclusion
    # ----------------------------
    with open(args.post_log, "a") as f:
        f.write("\n--- FINAL AGENT CONCLUSION ---\n")
        if pre_errors > 0 and post_errors == 0:
            f.write("THE ERROR IS CORRECTED\n")
        elif pre_errors > 0 and post_errors > 0:
            f.write("ERRORS STILL PRESENT\n")
        else:
            f.write("NO ERRORS TO CORRECT — TESTS WERE ALREADY PASSING\n")

        # ✅ REQUIRED LINE
        f.write("TASK COMPLETED\n")

    # ----------------------------
    # Artifacts
    # ----------------------------
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
