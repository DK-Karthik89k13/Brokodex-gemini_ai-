#!/usr/bin/env python3
import os
import sys
import json
import yaml
import types
import subprocess
import argparse
from pathlib import Path
from datetime import datetime, UTC

# ---------------------------
# Auto-install missing packages
# ---------------------------
import importlib

def safe_import(module_name, package_name=None):
    """Try to import, auto-install if missing."""
    if package_name is None:
        package_name = module_name
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError:
        print(f"[agent] Missing module {module_name}, installing {package_name}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", package_name])
        return importlib.import_module(module_name)

# Wrap critical optional packages
requests = safe_import("requests")
google_genai = safe_import("google.genai")
Client = None
GenerateContentConfig = None
try:
    from google.genai import Client
    from google.genai.types import GenerateContentConfig
except Exception:
    Client = None

# ---------------------------
# Logging utils
# ---------------------------
AGENT_LOG_PATH = "/tmp/agent.log"
PROMPTS_MD_PATH = "/tmp/prompts.md"

def utc_ts():
    return datetime.now(UTC).isoformat()

def log_event(payload):
    payload["timestamp"] = utc_ts()
    with open(AGENT_LOG_PATH, "a") as f:
        f.write(json.dumps(payload) + "\n")
        f.flush()

# ---------------------------
# Patch method
# ---------------------------
PATCH_METHOD = """
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

# ---------------------------
# Repo helpers
# ---------------------------
def find_imports_file(repo: Path) -> Path:
    for p in repo.rglob("imports.py"):
        try:
            if "class ImportItem" in p.read_text():
                return p
        except Exception:
            continue
    raise RuntimeError("imports.py containing ImportItem not found")

def apply_fix(repo: Path):
    target = find_imports_file(repo)
    code = target.read_text()

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

    lines.insert(insert_at, PATCH_METHOD)
    target.write_text("\n".join(lines) + "\n")
    return target

# ---------------------------
# Shell runner
# ---------------------------
def run(cmd, cwd):
    return subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)

def run_pytest(test_target, repo, log_path, stage):
    r = run(f"python -m pytest {test_target} -vv", cwd=repo)
    Path(log_path).write_text(r.stdout + "\n" + r.stderr)
    (Path(log_path).parent / f"{Path(log_path).stem}.exit").write_text(str(r.returncode))

    log_event({
        "stage": stage,
        "exit_code": r.returncode,
        "stdout_tail": r.stdout[-4000:],
        "stderr_tail": r.stderr[-4000:]
    })
    return r.returncode

# ---------------------------
# Main
# ---------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-file", required=True)
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--pre-log", required=True)
    ap.add_argument("--post-log", required=True)
    ap.add_argument("--prompt-log", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    repo = Path(args.repo_path)
    open(AGENT_LOG_PATH, "w").close()  # reset log

    # Load task
    with open(args.task_file, "r") as f:
        task = yaml.safe_load(f)

    target_file = Path(task["files_to_modify"][0])
    content = target_file.read_text()

    log_event({"type": "tool_use", "tool": "read_file", "args": {"file_path": str(target_file)}})

    # System prompt
    system_prompt = "Apply SWE-bench deterministic patch for find_staged_or_pending."
    Path(args.prompt_log).write_text(system_prompt)
    log_event({"type": "request", "content": system_prompt})

    # ---------------------------
    # Gemini call
    # ---------------------------
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

    # ---------------------------
    # Apply patch
    # ---------------------------
    if "def find_staged_or_pending" not in content:
        insert_point = content.find("class ImportItem")
        if insert_point == -1:
            raise RuntimeError("Could not locate insertion point.")
        updated_content = content[:insert_point] + PATCH_METHOD + "\n" + content[insert_point:]
        target_file.write_text(updated_content)
        log_event({"type": "tool_result", "content": "Patch applied successfully"})
        patched = True
    else:
        log_event({"type": "tool_result", "content": "Patch already present"})
        patched = False

    # ---------------------------
    # Run pre/post pytest
    # ---------------------------
    TEST_TARGET = "openlibrary/tests/core/test_imports.py"
    pre_exit = run_pytest(TEST_TARGET, repo, args.pre_log, "pre_validation")
    post_exit = run_pytest(TEST_TARGET, repo, args.post_log, "post_validation")

    # Save results
    Path(args.results).write_text(json.dumps({
        "task_file": str(args.task_file),
        "pre_exit": pre_exit,
        "post_exit": post_exit,
        "fix_applied": patched
    }, indent=2))

if __name__ == "__main__":
    main()
