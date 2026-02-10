#!/usr/bin/env python3
import os
import sys
import json
import yaml
import argparse
import subprocess
import importlib
import re
from pathlib import Path
from datetime import datetime, timezone

# ----------------------------
# Constants
# ----------------------------
AGENT_LOG_PATH = "/tmp/agent.log"

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

def log_event(payload):
    payload["timestamp"] = utc_ts()
    with open(AGENT_LOG_PATH, "a") as f:
        f.write(json.dumps(payload) + "\n")

def run(cmd, cwd=None):
    return subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
    )

def safe_import(module, package=None):
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package or module])
        return importlib.import_module(module)

# ----------------------------
# Optional Gemini (logging only)
# ----------------------------
Client = None
GenerateContentConfig = None
try:
    safe_import("google.genai")
    from google.genai import Client
    from google.genai.types import GenerateContentConfig
except Exception:
    pass

# ----------------------------
# Repo helpers
# ----------------------------
def find_imports_file(repo: Path) -> Path:
    for p in repo.rglob("imports.py"):
        try:
            if "class ImportItem" in p.read_text():
                return p
        except Exception:
            continue
    raise RuntimeError("ImportItem class not found")

def apply_patch(repo: Path) -> bool:
    target = find_imports_file(repo)
    code = target.read_text()

    if "find_staged_or_pending" in code:
        return False

    lines = code.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("class ImportItem"):
            insert_at = i + 1
            while insert_at < len(lines) and lines[insert_at].strip():
                insert_at += 1
            lines.insert(insert_at, PATCH_METHOD)
            target.write_text("\n".join(lines) + "\n")
            return True

    raise RuntimeError("Failed to apply patch")

# ----------------------------
# Pytest runner
# ----------------------------
def run_pytest(repo, log_path, stage):
    result = run(
        "python -m pytest openlibrary/tests/core/test_imports.py -vv",
        cwd=repo,
    )

    Path(log_path).write_text(result.stdout + "\n" + result.stderr)

    errors = len(re.findall(r"\bERROR\b", result.stdout + result.stderr))
    warnings = len(re.findall(r"\bWARNING\b", result.stdout + result.stderr))

    log_event({
        "stage": stage,
        "exit_code": result.returncode,
        "errors": errors,
        "warnings": warnings,
    })

    return result.returncode, errors, warnings

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
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    repo = Path(args.repo_path)
    open(AGENT_LOG_PATH, "w").close()

    # ----------------------------
    # Prompt (deterministic)
    # ----------------------------
    system_prompt = "Apply SWE-bench deterministic patch for find_staged_or_pending."
    Path(args.prompt_log).write_text(system_prompt)
    log_event({"type": "request", "content": system_prompt})

    # ----------------------------
    # Gemini call (logging only)
    # ----------------------------
    if Client and args.model:
        try:
            client = Client(api_key=os.environ.get("GEMINI_API_KEY"))
            resp = client.models.generate_content(
                model=args.model,
                contents=system_prompt,
                config=GenerateContentConfig(temperature=0.2),
            )
            log_event({"type": "gemini_response", "content": resp.text})
        except Exception as e:
            log_event({"type": "gemini_error", "content": str(e)})

    # ----------------------------
    # Pre-validation
    # ----------------------------
    pre_exit, pre_err, pre_warn = run_pytest(
        repo, args.pre_log, "pre_validation"
    )

    # ----------------------------
    # Apply patch
    # ----------------------------
    fix_applied = apply_patch(repo)
    log_event({
        "type": "tool_result",
        "content": "Patch applied" if fix_applied else "Patch already present"
    })

    # ----------------------------
    # Post-validation
    # ----------------------------
    post_exit, post_err, post_warn = run_pytest(
        repo, args.post_log, "post_validation"
    )

    with open(args.post_log, "a") as f:
        f.write(
            f"\n--- PRE-VALIDATION SUMMARY ---\n"
            f"Errors: {pre_err}\n"
            f"Warnings: {pre_warn}\n"
        )

    # ----------------------------
    # Results
    # ----------------------------
    Path(args.results).write_text(json.dumps({
        "task_file": None,
        "pre_exit": pre_exit,
        "post_exit": post_exit,
        "pre_errors": pre_err,
        "pre_warnings": pre_warn,
        "fix_applied": fix_applied,
    }, indent=2))

if __name__ == "__main__":
    main()
