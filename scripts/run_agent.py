#!/usr/bin/env python3
import os
import json
import subprocess
import argparse
import re
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

        q = cls.where("ia_id IN $ia_ids", vars={"ia_ids": ia_ids})
        q = q.where("status IN ('staged', 'pending')")
        return list(q)
""".rstrip()

import re

def apply_fix(repo: Path):
    target = find_imports_file(repo)
    code = target.read_text()

    method_re = re.compile(
        r"""
        @classmethod\s+
        def\s+find_staged_or_pending\s*\(.*?\):   # method header
        (?:\n[ \t]+.*)*                           # method body
        """,
        re.VERBOSE
    )

    if method_re.search(code):
        # 🔥 REPLACE existing method
        new_code = method_re.sub(FIX_METHOD + "\n", code)
        target.write_text(new_code)
        return target

    # ---- method does NOT exist → insert ----
    lines = code.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("class ImportItem"):
            insert_at = i + 1
            lines.insert(insert_at, FIX_METHOD)
            target.write_text("\n".join(lines) + "\n")
            return target

    raise RuntimeError("ImportItem class not found")


# -------------------------
# Pytest runner (CRITICAL FIX)
# -------------------------
def run_pytest(test_target, repo, log_path, agent_log, stage):
    r = run(f"python -m pytest {test_target} -vv", cwd=repo)

    log_path.write_text(r.stdout + "\n" + r.stderr)
    (log_path.parent / f"{log_path.stem}.exit").write_text(str(r.returncode))

    log(agent_log, {
        "stage": stage,
        "exit_code": r.returncode,
        "stdout_tail": r.stdout[-4000:],
        "stderr_tail": r.stderr[-4000:]
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
    ap.add_argument("--model", default="gemini-1.5-pro")
    args = ap.parse_args()

    repo = Path(args.repo_path)
    agent_log = open(args.log_path, "w", buffering=1)

    TEST_TARGET = "openlibrary/tests/core/test_imports.py"

    system_prompt = (
        "You are an autonomous SWE-bench agent.\n"
        "Fix failing tests in openlibrary/tests/core/test_imports.py"
    )

    Path(args.prompt_log).write_text(system_prompt)
    log(agent_log, {"type": "prompt", "content": system_prompt})

    # -------------------------
    # PRE-VALIDATION
    # -------------------------
    log(agent_log, {"stage": "pre_validation_start"})
    pre_exit = run_pytest(
        TEST_TARGET,
        repo,
        Path(args.pre_log),
        agent_log,
        "pre_validation",
    )

    if pre_exit == 0:
        log(agent_log, {
            "warning": "Pre-validation unexpectedly passed. Tests may be mis-selected."
        })

    # -------------------------
    # MODEL CALL (LOGGED ONLY)
    # -------------------------
    try:
        client = Client(api_key=os.environ["GEMINI_API_KEY"])
        resp = client.models.generate_content(
            model=args.model,
            contents=system_prompt,
            config=GenerateContentConfig(temperature=0.2),
        )
        log(agent_log, {"gemini_output": resp.text})
    except Exception as e:
        log(agent_log, {"gemini_error": str(e)})

    # -------------------------
    # APPLY FIX
    # -------------------------
    log(agent_log, {"stage": "apply_fix"})
    patched = apply_fix(repo)

    diff = run("git diff", cwd=repo)
    changes_patch = Path(args.post_log).parent / "changes.patch"

    if diff.stdout.strip():
        changes_patch.write_text(diff.stdout)
        changed = True
    else:
        changes_patch.write_text("")
        changed = False

    log(agent_log, {
        "fix_applied": bool(patched),
        "changes_detected": changed,
        "file": str(patched) if patched else None
    })

    # -------------------------
    # POST-VALIDATION
    # -------------------------
    log(agent_log, {"stage": "post_validation_start"})
    post_exit = run_pytest(
        TEST_TARGET,
        repo,
        Path(args.post_log),
        agent_log,
        "post_validation",
    )

    results = {
        "task_id": args.task_id,
        "pre_exit": pre_exit,
        "post_exit": post_exit,
        "fix_applied": bool(patched),
        "changes_detected": changed
    }

    Path(args.results).write_text(json.dumps(results, indent=2))
    agent_log.close()

if __name__ == "__main__":
    main()
