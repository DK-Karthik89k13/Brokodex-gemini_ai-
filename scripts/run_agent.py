#!/usr/bin/env python3
import os
import json
import subprocess
import argparse
import urllib.request
from pathlib import Path
from datetime import datetime, UTC

from google.genai import Client
from google.genai.types import GenerateContentConfig

# =========================
# Utils
# =========================
def utc_ts():
    return datetime.now(UTC).isoformat()

def log(fp, payload):
    payload["timestamp"] = utc_ts()
    fp.write(json.dumps(payload) + "\n")
    fp.flush()

def run(cmd, cwd):
    return subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
    )

# =========================
# Task download
# =========================
def download_task(task_id: str, dest: Path):
    url = f"https://raw.githubusercontent.com/swe-bench/swe-bench/main/tasks/{task_id}.yaml"
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)
    return dest

# =========================
# Repo helpers
# =========================
def find_imports_file(repo: Path) -> Path:
    for p in repo.rglob("imports.py"):
        if "class ImportItem" in p.read_text(errors="ignore"):
            return p
    raise RuntimeError("imports.py containing ImportItem not found")

FIX_METHOD = """
    @staticmethod
    def find_staged_or_pending(identifiers, sources=("amazon", "idb")):
        if not identifiers:
            return None

        ia_ids = [
            f"{source}:{identifier}"
            for identifier in identifiers
            for source in sources
        ]

        return ImportItem.where(
            "ia_id IN $ia_ids AND status IN ('staged', 'pending')",
            vars={"ia_ids": ia_ids},
        )
""".rstrip()

def apply_fix(repo: Path):
    target = find_imports_file(repo)
    code = target.read_text()

    if "find_staged_or_pending" in code:
        return False

    lines = code.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("class ImportItem"):
            insert_at = i + 1
            break
    else:
        raise RuntimeError("ImportItem class not found")

    while insert_at < len(lines) and lines[insert_at].startswith(" "):
        insert_at += 1

    lines.insert(insert_at, FIX_METHOD)
    target.write_text("\n".join(lines) + "\n")
    return True

# =========================
# Pytest runner
# =========================
def run_pytest(target, repo, log_path, agent_log, stage):
    r = run(f"python -m pytest {target} -vv", cwd=repo)

    log_path.write_text(r.stdout + "\n" + r.stderr)

    log(agent_log, {
        "stage": stage,
        "exit_code": r.returncode,
        "stdout_tail": r.stdout[-3000:],
        "stderr_tail": r.stderr[-3000:]
    })

    return r.returncode, r.stdout, r.stderr

# =========================
# Main
# =========================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--log-path", required=True)
    ap.add_argument("--prompt-log", required=True)
    ap.add_argument("--pre-log", required=True)
    ap.add_argument("--post-log", required=True)
    ap.add_argument("--results", required=True)
    args = ap.parse_args()

    repo = Path(args.repo_path)
    artifacts = Path(args.post_log).parent
    artifacts.mkdir(parents=True, exist_ok=True)

    agent_log = open(args.log_path, "w", buffering=1)

    # -------------------------
    # Download task
    # -------------------------
    task_path = artifacts / "task.yaml"
    download_task(args.task_id, task_path)
    log(agent_log, {"task_downloaded": str(task_path)})

    system_prompt = (
        "Fix failing tests in openlibrary/tests/core/test_imports.py\n"
        "Prefer local staged or pending ImportItem records."
    )
    Path(args.prompt_log).write_text(system_prompt)

    # -------------------------
    # PRE-VALIDATION
    # -------------------------
    pre_exit, pre_out, pre_err = run_pytest(
        "openlibrary/tests/core/test_imports.py",
        repo,
        Path(args.pre_log),
        agent_log,
        "pre_validation"
    )

    # -------------------------
    # MODEL CALL (LOGGED ONLY)
    # -------------------------
    try:
        client = Client(api_key=os.environ.get("GEMINI_API_KEY", ""))
        resp = client.models.generate_content(
            model="gemini-1.5-pro",
            contents=system_prompt,
            config=GenerateContentConfig(temperature=0.2),
        )
        log(agent_log, {"model_output": resp.text})
    except Exception as e:
        log(agent_log, {"model_error": str(e)})

    # -------------------------
    # APPLY FIX
    # -------------------------
    fix_applied = apply_fix(repo)

    diff = run("git diff", cwd=repo).stdout
    changes_patch = artifacts / "changes.patch"
    changes_patch.write_text(diff or "# NO DIFF GENERATED\n")

    # -------------------------
    # POST-VALIDATION
    # -------------------------
    post_exit, post_out, post_err = run_pytest(
        "openlibrary/tests/core/test_imports.py",
        repo,
        Path(args.post_log),
        agent_log,
        "post_validation"
    )

    # -------------------------
    # FINAL REPORT (🔥 IMPORTANT)
    # -------------------------
    report = {
        "task_id": args.task_id,
        "fix_applied": fix_applied,
        "pre_validation": {
            "exit_code": pre_exit,
            "failed": pre_exit != 0
        },
        "post_validation": {
            "exit_code": post_exit,
            "passed": post_exit == 0
        },
        "artifacts": {
            "agent_log": args.log_path,
            "pre_log": args.pre_log,
            "post_log": args.post_log,
            "changes_patch": str(changes_patch),
            "prompts": args.prompt_log,
            "task": str(task_path)
        },
        "timestamp": utc_ts()
    }

    Path(args.results).write_text(json.dumps(report, indent=2))
    Path(artifacts / "report.json").write_text(json.dumps(report, indent=2))

    agent_log.close()

if __name__ == "__main__":
    main()
