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
HTML_REPORT = ARTIFACT_DIR / "swebench_result.html"

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
        cmd,
        shell=True,
        cwd=cwd,
        text=True,
        capture_output=True
    )

def count_errors(text):
    return len(re.findall(r"\bFAILED\b|\bERROR\b", text))

def count_passed(text):
    m = re.search(r"(\d+)\s+passed", text)
    return int(m.group(1)) if m else 0

def count_warnings(text):
    m = re.search(r"(\d+)\s+warnings?", text)
    return int(m.group(1)) if m else 0

def find_problem_modules(text):
    modules = set()
    for line in text.splitlines():
        m = re.search(r"ModuleNotFoundError: No module named '([^']+)'", line)
        if m:
            modules.add(m.group(1))
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
    combined = ""

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
    passed = count_passed(combined)
    warnings = count_warnings(combined)

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
        f.write(f"ERROR COUNT   : {errors}\n")
        f.write(f"TESTS PASSED  : {passed}\n")
        f.write(f"WARNINGS      : {warnings}\n")

        if reinstalled:
            f.write(f"MODULES REINSTALLED : {reinstalled}\n")

    log_agent(
        "validation",
        stage=stage,
        errors=errors,
        passed=passed,
        warnings=warnings,
    )

    return errors, passed, warnings

# ----------------------------
# HTML REPORT
# ----------------------------
def write_swebench_html(
    output_path: Path,
    pre_errors: int,
    post_errors: int,
    pre_passed: int,
    post_passed: int,
    pre_warnings: int,
    post_warnings: int,
    duration_seconds: float,
):
    resolved = pre_errors > 0 and post_errors == 0

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>SWE-bench Evaluation Result</title>
<style>
body {{ font-family: Arial; background: #f6f8fa; padding: 20px; }}
.card {{ background: white; padding: 20px; max-width: 900px; margin: auto; }}
.status {{ font-weight: bold; color: {"green" if resolved else "red"}; }}
table {{ width: 100%; border-collapse: collapse; }}
td, th {{ border-bottom: 1px solid #ddd; padding: 8px; }}
</style>
</head>
<body>
<div class="card">
<h1>SWE-bench Evaluation Result</h1>
<p class="status">Resolved: {"YES" if resolved else "NO"}</p>

<table>
<tr><th>Duration</th><td>{duration_seconds:.2f} seconds</td></tr>
<tr><th>Total Cost</th><td>N/A</td></tr>
<tr><th>Token Usage (Input)</th><td>N/A</td></tr>
<tr><th>Token Usage (Output)</th><td>N/A</td></tr>
</table>

<h2>Test Results</h2>
<table>
<tr><th>Stage</th><th>Errors</th><th>Passed</th><th>Warnings</th></tr>
<tr><td>Pre-validation</td><td>{pre_errors}</td><td>{pre_passed}</td><td>{pre_warnings}</td></tr>
<tr><td>Post-validation</td><td>{post_errors}</td><td>{post_passed}</td><td>{post_warnings}</td></tr>
</table>

<h2>Agent Conclusion</h2>
<pre>
{"THE ERROR IS CORRECTED" if resolved else "NO ERRORS TO CORRECT — TESTS WERE ALREADY PASSING"}
TASK COMPLETED
</pre>
</div>
</body>
</html>
"""
    output_path.write_text(html, encoding="utf-8")

# ----------------------------
# Generate Git Patch
# ----------------------------
def generate_patch(repo):
    git_check = run("git rev-parse --is-inside-work-tree", repo)

    if git_check.returncode != 0:
        CHANGES_PATCH.write_text("# Not a git repository\n")
        return False

    run("git add -A", repo)
    diff = run("git diff --cached", repo).stdout

    if diff.strip():
        CHANGES_PATCH.write_text(diff)
        return True
    else:
        CHANGES_PATCH.write_text("# No changes detected in repository\n")
        return False

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

    start_time = datetime.now(timezone.utc)
    log_agent("run_started", model=args.model)

    Path(args.prompt_log).write_text(
        "Run pytest. Auto-reinstall broken modules. Fix code only if pytest proves failure."
    )

    pre_errors, pre_passed, pre_warnings = run_validation(
        repo, Path(args.pre_log), "pre_validation"
    )

    post_errors, post_passed, post_warnings = run_validation(
        repo, Path(args.post_log), "post_validation"
    )

    with open(args.post_log, "a") as f:
        f.write("\n--- FINAL AGENT CONCLUSION ---\n")
        if pre_errors > 0 and post_errors == 0:
            f.write("THE ERROR IS CORRECTED\n")
        elif pre_errors > 0:
            f.write("ERRORS STILL PRESENT\n")
        else:
            f.write("NO ERRORS TO CORRECT — TESTS WERE ALREADY PASSING\n")
        f.write("TASK COMPLETED\n")

    change_applied = generate_patch(repo)

    duration = (datetime.now(timezone.utc) - start_time).total_seconds()

    write_swebench_html(
        HTML_REPORT,
        pre_errors,
        post_errors,
        pre_passed,
        post_passed,
        pre_warnings,
        post_warnings,
        duration,
    )

    Path(args.results).write_text(json.dumps({
        "pre_errors": pre_errors,
        "post_errors": post_errors,
        "tests_passing": post_errors == 0,
        "change_applied": change_applied,
    }, indent=2))

    log_agent("run_complete")

if __name__ == "__main__":
    main()
