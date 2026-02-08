#!/usr/bin/env python3
import os
import json
import argparse
import subprocess
import re
from datetime import datetime, timezone

import google.genai as genai

# ---------------- globals ----------------
REPO_ROOT = None
AGENT_LOG = None

# ---------------- logging ----------------
def log(entry):
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    with open(AGENT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

# ---------------- tools ----------------
def read_file(path):
    full = os.path.join(REPO_ROOT, path)
    try:
        with open(full, encoding="utf-8") as f:
            return {"success": True, "content": f.read()}
    except Exception as e:
        return {"success": False, "error": str(e)}

def write_file(path, content):
    full = os.path.join(REPO_ROOT, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    return {"success": True}

def edit_file(path, old, new):
    full = os.path.join(REPO_ROOT, path)
    with open(full, encoding="utf-8") as f:
        text = f.read()
    if old not in text:
        return {"success": False, "error": "old text not found"}
    with open(full, "w", encoding="utf-8") as f:
        f.write(text.replace(old, new))
    return {"success": True}

def run_bash(cmd):
    r = subprocess.run(
        cmd,
        shell=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    return {
        "success": r.returncode == 0,
        "stdout": r.stdout,
        "stderr": r.stderr,
        "returncode": r.returncode,
    }

TOOLS = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "run_bash": run_bash,
}

# ---------------- main ----------------
def main():
    global REPO_ROOT, AGENT_LOG

    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--log-path", required=True)
    parser.add_argument("--prompt-log", required=True)
    args = parser.parse_args()

    REPO_ROOT = args.repo_path
    AGENT_LOG = args.log_path

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("❌ GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)

    # ✅ EXACT MODEL YOU ASKED FOR
    MODEL = "gemini-2.0-flash"

    test_cmd = (
        "python -m pytest "
        "openlibrary/tests/core/test_imports.py::"
        "TestImportItem::test_find_staged_or_pending -xvs"
    )

    # Pre-check
    if run_bash(test_cmd)["returncode"] == 0:
        print("✅ Test already passes. Exiting.")
        return

    system_prompt = """
You are a senior Python engineer fixing failing tests in OpenLibrary.

STRICT RULES:
- Respond ONLY with valid JSON
- No markdown
- No explanations
- One action at a time

Format EXACTLY:

{
  "tool": "read_file | write_file | edit_file | run_bash",
  "args": { ... }
}

Stop when tests pass.
""".strip()

    prompt = system_prompt
    log({"type": "request", "content": prompt})

    for i in range(15):
        print(f"--- Iteration {i + 1} ---")

        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
        )

        text = (response.text or "").strip()
        log({"type": "response", "content": text})

        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            prompt = "Invalid response. Return ONLY valid JSON."
            continue

        try:
            action = json.loads(match.group(0))
            tool = action["tool"]
            args = action["args"]
        except Exception as e:
            prompt = f"Invalid JSON structure: {e}"
            continue

        if tool not in TOOLS:
            prompt = f"Unknown tool: {tool}"
            continue

        result = TOOLS[tool](**args)
        log({
            "type": "tool_use",
            "tool": tool,
            "args": args,
            "result": result,
        })

        if run_bash(test_cmd)["returncode"] == 0:
            print("✅ FIX SUCCESSFUL")
            log({"type": "final", "status": "success"})
            return

        prompt = f"Tool result:\n{json.dumps(result, indent=2)}"

    raise RuntimeError("❌ Agent failed to fix task")

if __name__ == "__main__":
    main()
