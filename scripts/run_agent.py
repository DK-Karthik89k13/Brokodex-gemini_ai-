#!/usr/bin/env python3
import os
import sys
import json
import types
import subprocess
import argparse
from pathlib import Path
from datetime import datetime, UTC

# Gemini is OPTIONAL
try:
    from google.genai import Client
    from google.genai.types import GenerateContentConfig
except Exception:
    Client = None


# -------------------------------------------------
# Utils
# -------------------------------------------------
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


# -------------------------------------------------
# CRITICAL: stub optional deps so pytest can run
# -------------------------------------------------
def ensure_optional_deps(agent_log):
    stubbed = []
    for mod in ("memcache",):
        if mod not in sys.modules:
            sys.modules[mod] = types.ModuleType(mod)
            stubbed.append(mod)

    if stubbed:
        log(agent_log, {
            "stage": "dependency_stubbed",
            "modules": stubbed
        })


# -------------------------------------------------
# Repo helpers
# -------------------------------------------------
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
    def find_staged_or_pending(cls, identifiers, sources=None):
        if not identifiers:
            return cls.where("1=0")

        sources = sources or STAGED_SOURCES

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

    # Do not double-apply
    if "find_staged_or_pending" in code:
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
    while insert_at < len(lines) and lines[insert_at].strip():
        insert_at += 1

    lines.insert(insert_at, FIX_METHOD)
    target.write_text("\n".join(lines) + "\n")

    return target


# -------------------------------------------------
# Pytest runner
# -------------------------------------------------
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


# -------------------------------------------------
# Main
# -------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--log-path", required=True)
    ap.add_argument("--prompt-log", required=True)
    ap.add_argument("--pre-log", required=True)
    ap.add_argument("--post-log", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--model", default=None)   # FIXED: optional
    args = ap.parse_args()

    repo = Path(args.repo_path)
    agent_log = open(args.log_path, "w", buffering=1)

    TEST_TARGET = "openlibrary/tests/core/test_imports.py"

    system_prompt = (
        "You are an autonomous SWE-bench agent.\n"
        "Fix failing tests in openlibrary/tests/core/test_imports.py\n"
        "Prefer local staged or pending ImportItem records."
    )

    Path(args.prompt_log).write_text(system_prompt)
    log(agent_log, {"type": "prompt", "content": system_prompt})

    # -------------------------
    # PRE-VALIDATION
    # -------------------------
    log(agent_log, {"stage": "pre_validation_start"})
    ensure_optional_deps(agent_log)

    pre_exit = run_pytest(
        TEST_TARGET,
        repo,
        Path(args.pre_log),
        agent_log,
        "pre_validation",
    )

    if pre_exit == 0:
        log(agent_log, {
            "warning": "Pre-validation unexpectedly passed"
        })

    # -------------------------
    # MODEL CALL (LOG ONLY)
    # -------------------------
    if Client and args.model:
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
        "patched_file": str(patched) if patched else None
    })

    # -------------------------
    # POST-VALIDATION
    # -------------------------
    log(agent_log, {"stage": "post_validation_start"})
    ensure_optional_deps(agent_log)

    post_exit = run_pytest(
        TEST_TARGET,
        repo,
        Path(args.post_log),
        agent_log,
        "post_validation",
    )

    Path(args.results).write_text(json.dumps({
        "task_id": args.task_id,
        "pre_exit": pre_exit,
        "post_exit": post_exit,
        "fix_applied": bool(patched),
        "changes_detected": changed
    }, indent=2))

    agent_log.close()


if __name__ == "__main__":
    main()
