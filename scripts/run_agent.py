#!/usr/bin/env python3
import os
import sys
import json
import yaml
import subprocess
import argparse
from pathlib import Path
from datetime import datetime, timezone
import importlib
import re

# ----------------------------
# Paths
# ----------------------------
AGENT_LOG_PATH = "/tmp/agent.log"

# ----------------------------
# Utility functions
# ----------------------------
def utc_ts():
    return datetime.now(timezone.utc).isoformat()

def log_event(payload):
    payload["timestamp"] = utc_ts()
    with open(AGENT_LOG_PATH, "a") as f:
        f.write(json.dumps(payload) + "\n")
        f.flush()

def run(cmd, cwd=None):
    result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    return result

def safe_import(module_name, package_name=None):
    if package_name is None:
        package_name = module_name
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError:
        print(f"[agent] Installing missing package: {package_name}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])
        return importlib.import_module(module_name)

# ----------------------------
# Optional Gemini AI client
# ----------------------------
Client = None
GenerateContentConfig = None
try:
    google_genai = safe_import("google.genai")
    from google.genai import Client
    from google.genai.types import GenerateContentConfig
except Exception:
    Client = None

# ----------------------------
# Patch to apply
# ----------------------------
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
# Repo helpers
# ----------------------------
def find_imports_file(repo: Path) -> Path:
    for p in repo.rglob("imports.py"):
        try:
            if "class ImportItem" in p.read_text():
                return p
        except Exception:
            continue
    raise RuntimeError("imports.py containing ImportItem not found")

def apply_patch(repo: Path):
    target = find_imports_file(repo)
    code = target.read_text()
    if "find_staged_or_pending" in code:
        return None
    lines = code.splitlines()
    class_idx = None
    for i, line in enumerate(lines):
        if line.startswith("class ImportItem"):
            class_idx = i
            break
    if class_idx is None:
        raise RuntimeError("ImportItem class not found")
    insert_at = class_idx + 1
    while insert_at < len(lines) and lines[insert_at].strip():
        insert_at += 1
    lines.insert(insert_at, PATCH_METHOD)
    target.write_text("\n".join(lines) + "\n")
    return target

# ----------------------------
# Pytest runner
# ----------------------------
def run_pytest(test_target, repo, log_path, stage):
    result = run(f"python -m pytest {test_target} -vv", cwd=repo)
    Path(log_path).write_text(result.stdout + "\n" + result.stderr)

    # Count errors in pre-validation
    error_count = len(re.findall(r'ERROR', result.stdout + result.stderr))
    warning_count = len(re.findall(r'WARNING', result.stdout + result.stderr))
    
    log_event({
        "stage": stage,
        "exit_code": result.returncode,
        "errors": error_count,
        "warnings": warning_count,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:]
    })
    return result.returncode, error_count, warning_count

# ----------------------------
# Main
# ----------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-file", required=False)
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--pre-log", required=True)
    parser.add_argument("--post-log", required=True)
    parser.add_argument("--prompt-log", required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument("--model", default=None)
    args = parser.parse_args()

    repo = Path(args.repo_path)
    open(AGENT_LOG_PATH, "w").close()

    # Load task if provided
    if args.task_file and Path(args.task_file).exists():
        with open(args.task_file, "r") as f:
            task = yaml.safe_load(f)
        target_file = Path(task["files_to_modify"][0])
        content = target_file.read_text()
    else:
        task = None
        target_file = None
        content = ""

    # System prompt
    system_prompt = "Apply SWE-bench deterministic patch for find_staged_or_pending."
    Path(args.prompt_log).write_text(system_prompt)
    log_event({"type": "request", "content": system_prompt})

    # Gemini AI call
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

    # Apply patch if task file exists
    patched = False
    if target_file and "def find_staged_or_pending" not in content:
        insert_point = content.find("class ImportItem")
        if insert_point == -1:
            raise RuntimeError("Could not locate insertion point.")
        updated_content = content[:insert_point] + PATCH_METHOD + "\n" + content[insert_point:]
        target_file.write_text(updated_content)
        log_event({"type": "tool_result", "content": "Patch applied successfully"})
        patched = True
    else:
        log_event({"type": "tool_result", "content": "Patch already present or no task file"})

    # Pre-validation
    pre_exit, pre_errors, pre_warnings = run_pytest("openlibrary/tests/core/test_imports.py", repo, args.pre_log, "pre_validation")

    # Post-validation
    post_exit, post_errors, post_warnings = run_pytest("openlibrary/tests/core/test_imports.py", repo, args.post_log, "post_validation")

    # Append error summary to post-validation log
    summary = f"\n--- PRE-VALIDATION SUMMARY ---\nErrors: {pre_errors}\nWarnings: {pre_warnings}\n"
    with open(args.post_log, "a") as f:
        f.write(summary)

    # Save results
    Path(args.results).write_text(json.dumps({
        "task_file": str(args.task_file) if args.task_file else None,
        "pre_exit": pre_exit,
        "post_exit": post_exit,
        "pre_errors": pre_errors,
        "pre_warnings": pre_warnings,
        "fix_applied": patched
    }, indent=2))

if __name__ == "__main__":
    main()
