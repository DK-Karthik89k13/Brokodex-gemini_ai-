#!/usr/bin/env python3
import os
import json
import argparse
import subprocess
import datetime
from pathlib import Path

from google.genai import Client
from google.genai.types import GenerateContentConfig

# -------------------------
# Utilities
# -------------------------

def utc_ts():
    return datetime.datetime.now(datetime.UTC).isoformat()

def log(fp, payload):
    payload["timestamp"] = utc_ts()
    fp.write(json.dumps(payload) + "\n")
    fp.flush()

def run_bash(cmd, cwd):
    return subprocess.check_output(
        cmd, shell=True, cwd=cwd, text=True, stderr=subprocess.STDOUT
    )

def read_file(path):
    return Path(path).read_text()

def write_file(path, content):
    Path(path).write_text(content)
    return "ok"

# -------------------------
# Agent
# -------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--log-path", required=True)
    ap.add_argument("--prompt-log", required=True)
    ap.add_argument("--model", default="gemini-1.5-flash")
    args = ap.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    client = Client(api_key=api_key)

    system_prompt = f"""
You are an autonomous SWE-bench coding agent.

Task ID:
{args.task_id}

Repository path:
{args.repo_path}

Goal:
Fix the failing test:
openlibrary/tests/core/test_imports.py::TestImportItem::test_find_staged_or_pending

Rules:
- Prefer staged/local ISBN records
- Modify code directly in the repository
- Output a unified git diff when ready
- Stop after producing the fix
""".strip()

    Path(args.prompt_log).write_text(system_prompt)

    logf = open(args.log_path, "w", buffering=1)

    for iteration in range(1, 10):
        log(logf, {"type": "iteration", "step": iteration})

        response = client.models.generate_content(
            model=args.model,
            contents=system_prompt,
            config=GenerateContentConfig(
                temperature=0.2,
                max_output_tokens=4096,
            ),
        )

        text = response.text or ""
        log(logf, {"type": "response", "content": text})

        # Apply patch if model outputs a diff
        if "diff --git" in text:
            patch = Path("/tmp/agent.patch")
            patch.write_text(text)
            run_bash(f"git apply {patch}", cwd=args.repo_path)
            log(logf, {"type": "status", "result": "fix_applied"})
            logf.close()
            return

    logf.close()
    raise RuntimeError("Agent failed to produce a fix")

if __name__ == "__main__":
    main()


