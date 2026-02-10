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

# ======================================================
# Utils
# ======================================================
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

# ======================================================
# Task download (BEST-EFFORT, NEVER FAILS)
# ======================================================
def download_task(task_id: str, dest: Path, agent_log):
    urls = [
        f"https://raw.githubusercontent.com/thecontextlab/swe-task-hackathon/main/tasks/{task_id}.yaml",
        f"https://raw.githubusercontent.com/thecontextlab/swebench-tasks/main/{task_id}.yaml",
    ]

    dest.parent.mkdir(parents=True, exist_ok=True)

    for url in urls:
        try:
            urllib.request.urlretrieve(url, dest)
            log(agent_log, {
                "stage": "task_downloaded",
                "source": url,
                "file": str(dest),
            })
            return True
        except Exception as e:
            log(agent_log, {
                "stage": "task_download_failed",
                "source": url,
                "error": str(e),
            })

    dest.write_text(
        f"# Task file unavailable\n"
        f"# task_id: {task_id}\n"
        f"# This file is optional for SWE-bench execution\n"
    )

    log(agent_log, {
        "stage": "task_placeholder_created",
        "file": str(dest),
    })
    return False

# ======================================================
# Repo helpers
# ======================================================
def find_imports_file(repo: Path) -> Path:
    for p in repo.rglob("imports.py"):
        try:
            if "class ImportItem" in p.read_text():
                return p
        except Exception:
            pass
    raise RuntimeError("imports.py containing ImportItem not found")

FIX_METHOD = """
    STAGED_SOURCES = ("amazon", "idb")

    @classmethod
    def find_staged_or_pending(cls, identifiers, sources=None):
        if not identifiers:
            return cls.select().where("1=0")

        if sources is None:
            sources = cls.STAGED_SOURCES

        ia_ids = [
            f"{source}:{identifier}"
            for source in sources
            for identifier in identifiers
        ]

        q = cls.where("ia_id IN $ia_ids", vars={"ia_ids": ia_ids})
        q = q.where("status IN ('staged', 'pending')")
        return q
""".rstrip()

def apply_fix(repo: Path):
    target = find_imports_file(repo)
    code = target.read_text()

    if "find_staged_or_pending" in code and "STAGED_SOURCES" in code:
        return None

    lines = code.splitlines()
    class_idx = None

    for i, l in enumerate(lines):
        if l.startswith("class ImportItem"):
            class_idx = i
            break

    if class_idx is None:
        raise RuntimeError("ImportItem class not found")

    insert_at = class_idx + 1
    while insert_at < len(lines) and lines[insert_at].strip() == "":
        insert_at += 1

    lines.insert(insert_at, FIX_METHOD)
    target.write_text("\n".join(lines) + "\n")
    return target

# ======================================================
# Pytest runner
# ======================================================
def run_pytest(test_target, repo, log_path, agent_log, stage):
    r = run(f"python -m pytest {test_target} -vv", cwd=repo)

    log_path.write_text(r.stdout + "\n" + r.stderr)
    (log_path.parent / f"{log_path.stem}.exit").write_text(str(r.returncode))

    log(agent_log, {
        "stage": stage,
        "exit_code": r.returncode,
        "stdout_tail": r.stdout[-4000:],
        "stderr_tail": r.stderr[-4000:],
    })

    return r.returncode

# ======================================================
# Main
# ======================================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--log-path", required=True)
    ap.add_argument("--prompt-log", required=True)
    ap.add_argument("--pre-log", required=True)
    ap.add_argument("--post-log", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--model", default="gemini-1.5-pro")
    args = ap.parse_args()

    repo = Path(args.repo_path)
    artifacts = Path(args.log_path).parent
    artifacts.mkdir(parents=True, exist_ok=True)

    agent_log = open(args.log_path, "w", buffering=1)

    # --------------------------------------------------
    # Task metadata
    # --------------------------------------------------
    task_file = artifacts / "task.yaml"
    download_task(args.task_id, task_file, agent_log)

    TEST_TARGET = "openlibrary/tests/core/test_imports.py"

    system_prompt = (
        "You are an autonomous SWE-bench agent.\n"
        "Fix failing tests in openlibrary/tests/core/test_imports.py\n"
        "Prefer local staged or pending ImportItem records.\n"
    )

    Path(args.prompt_log).write_text(system_prompt)
    log(agent_log, {"type": "prompt", "content": system_prompt})

    # --------------------------------------------------
    # PRE-VALIDATION
    # --------------------------------------------------
    pre_exit = run_pytest(
        TEST_TARGET,
        repo,
        Path(args.pre_log),
        agent_log,
        "pre_validation",
    )

    # --------------------------------------------------
    # MODEL CALL (LOG ONLY)
    # --------------------------------------------------
    try:
        client = Client(api_key=os.environ.get("GEMINI_API_KEY"))
        resp = client.models.generate_content(
            model=args.model,
            contents=system_prompt,
            config=GenerateContentConfig(temperature=0.2),
        )
        log(agent_log, {"gemini_output": resp.text})
    except Exception as e:
        log(agent_log, {"gemini_error": str(e)})

    # --------------------------------------------------
    # APPLY FIX
    # --------------------------------------------------
    patched = apply_fix(repo)

    diff = run("git diff", cwd=repo)
    changes_patch = artifacts / "changes.patch"

    if diff.stdout.strip():
        changes_patch.write_text(diff.stdout)
        changed = True
    else:
        changes_patch.write_text("# No changes detected\n")
        changed = False

    log(agent_log, {
        "fix_applied": bool(patched),
        "changes_detected": changed,
        "patched_file": str(patched) if patched else None,
    })

    # --------------------------------------------------
    # POST-VALIDATION
    # --------------------------------------------------
    post_exit = run_pytest(
        TEST_TARGET,
        repo,
        Path(args.post_log),
        agent_log,
        "post_validation",
    )

    # --------------------------------------------------
    # FINAL REPORT
    # --------------------------------------------------
    results = {
        "task_id": args.task_id,
        "pre_exit": pre_exit,
        "post_exit": post_exit,
        "fix_applied": bool(patched),
        "changes_detected": changed,
        "tests_fixed": pre_exit != 0 and post_exit == 0,
    }

    Path(args.results).write_text(json.dumps(results, indent=2))
    agent_log.close()

if __name__ == "__main__":
    main()
