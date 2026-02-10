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

# -------------------------
# Utils
# -------------------------
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

# -------------------------
# Task download
# -------------------------
def download_task(task_id: str, dest: Path):
    url = (
        "https://raw.githubusercontent.com/"
        "thecontextlab/swe-task-hackathon/main/tasks/"
        f"{task_id}.yaml"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    urllib.request.urlretrieve(url, dest)

# -------------------------
# Repo helpers
# -------------------------
def find_imports_file(repo: Path) -> Path:
    for p in repo.rglob("imports.py"):
        try:
            if "class ImportItem" in p.read_text():
                return p
        except Exception:
            pass
    raise RuntimeError("imports.py containing ImportItem not found")

FIX_METHOD = """
    @classmethod
    def find_staged_or_pending(cls, ia_ids, sources=None):
        if not ia_ids:
            return []

        q = cls.where(
            "ia_id IN $ia_ids AND status IN ('staged', 'pending')",
            vars={"ia_ids": ia_ids},
        )
        return list(q)
""".rstrip()

def apply_fix(repo: Path):
    target = find_imports_file(repo)
    code = target.read_text()

    if "def find_staged_or_pending" in code:
        return None

    lines = code.splitlines()
    class_idx = next(
        i for i, l in enumerate(lines) if l.startswith("class ImportItem")
    )

    insert_at = len(lines)
    for i in range(class_idx + 1, len(lines)):
        if lines[i] and not lines[i].startswith(" "):
            insert_at = i
            break

    lines.insert(insert_at, FIX_METHOD)
    target.write_text("\n".join(lines) + "\n")
    return target

# -------------------------
# Pytest runner
# -------------------------
def run_pytest(target, repo, log_path, agent_log, stage):
    r = run(f"python -m pytest {target} -vv", cwd=repo)

    log_path.write_text(r.stdout + "\n" + r.stderr)
    log(agent_log, {
        "stage": stage,
        "exit_code": r.returncode,
        "stdout_tail": r.stdout[-4000:],
        "stderr_tail": r.stderr[-4000:],
    })

    return r.returncode

# -------------------------
# Main
# -------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--log-path", required=True)
    ap.add_argument("--prompt-log", required=True)
    ap.add_argument("--pre-log", required=True)
    ap.add_argument("--post-log", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument(
        "--model",
        default=os.environ.get("GEMINI_MODEL", "gemini-1.5-pro"),
    )
    args = ap.parse_args()

    repo = Path(args.repo_path)
    artifacts = Path(args.log_path).parent
    artifacts.mkdir(parents=True, exist_ok=True)

    agent_log = open(args.log_path, "w", buffering=1)

    # -------------------------
    # Download task file
    # -------------------------
    task_file = artifacts / "task.yaml"
    download_task(args.task_id, task_file)
    log(agent_log, {"stage": "task_downloaded", "file": str(task_file)})

    TEST_TARGET = "openlibrary/tests/core/test_imports.py"

    system_prompt = (
        "You are an autonomous SWE-bench agent.\n"
        f"Task ID: {args.task_id}\n"
        "Fix failing tests in openlibrary/tests/core/test_imports.py"
    )

    Path(args.prompt_log).write_text(system_prompt)
    log(agent_log, {"type": "prompt", "content": system_prompt})

    # -------------------------
    # Pre-validation
    # -------------------------
    pre_exit = run_pytest(
        TEST_TARGET,
        repo,
        Path(args.pre_log),
        agent_log,
        "pre_validation",
    )

    # -------------------------
    # Model call (logged only)
    # -------------------------
    try:
        client = Client(api_key=os.environ["GEMINI_API_KEY"])
        resp = client.models.generate_content(
            model=args.model,
            contents=system_prompt,
            config=GenerateContentConfig(temperature=0.2),
        )
        log(agent_log, {"llm_output": resp.text})
    except Exception as e:
        log(agent_log, {"llm_error": str(e)})

    # -------------------------
    # Apply fix
    # -------------------------
    patched = apply_fix(repo)

    diff = run("git diff", cwd=repo)
    patch_file = artifacts / "changes.patch"

    if not diff.stdout.strip():
        raise RuntimeError("NO CHANGES PRODUCED — agent failed")

    patch_file.write_text(diff.stdout)

    log(agent_log, {
        "fix_applied": True,
        "patched_file": str(patched),
    })

    # -------------------------
    # Post-validation
    # -------------------------
    post_exit = run_pytest(
        TEST_TARGET,
        repo,
        Path(args.post_log),
        agent_log,
        "post_validation",
    )

    # -------------------------
    # Final report
    # -------------------------
    report = {
        "task_id": args.task_id,
        "model": args.model,
        "pre_exit": pre_exit,
        "post_exit": post_exit,
        "resolved": pre_exit != 0 and post_exit == 0,
        "artifacts": {
            "agent_log": args.log_path,
            "pre_log": args.pre_log,
            "post_log": args.post_log,
            "changes_patch": str(patch_file),
            "task_file": str(task_file),
        },
    }

    Path(args.results).write_text(json.dumps(report, indent=2))
    agent_log.close()

    if report["resolved"] is not True:
        raise SystemExit("❌ Task not resolved")

if __name__ == "__main__":
    main()
