import os
import json
import argparse
import subprocess

import google.generativeai as genai

from tools import read_file, write_file, edit_file, run_bash


# -----------------------------
# Helpers
# -----------------------------
def run(cmd, cwd):
    return subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        text=True,
        capture_output=True,
    )


TOOLS = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "run_bash": run_bash,
}

TOOL_INSTRUCTIONS = """
You may ONLY respond with valid JSON.

FORMAT:
{
  "tool": "<tool_name>",
  "args": { ... }
}

Available tools:
- read_file(path)
- write_file(path, content)
- edit_file(path, old, new)
- run_bash(command)

Rules:
- Use tools to fix the failing test
- Do NOT explain anything
- Do NOT add text outside JSON
"""


# -----------------------------
# Main
# -----------------------------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--log-path", required=True)
    parser.add_argument("--prompt-log", required=True)
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("❌ GEMINI_API_KEY not set")

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel("gemini-1.5-pro")

    test_cmd = (
        "python -m pytest "
        "openlibrary/tests/core/test_imports.py::TestImportItem::test_find_staged_or_pending -xvs"
    )

    result = run(test_cmd, args.repo_path)
    if result.returncode == 0:
        raise RuntimeError("❌ Test already passes — invalid task")

    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior engineer fixing a failing test.\n"
                "Stop ONLY after the test passes.\n\n"
                + TOOL_INSTRUCTIONS
            ),
        },
        {
            "role": "user",
            "content": (
                f"Repository path: {args.repo_path}\n\n"
                f"Failing test:\n{test_cmd}\n\n"
                f"Failure output:\n{result.stdout}\n{result.stderr}"
            ),
        },
    ]

    os.makedirs(os.path.dirname(args.log_path), exist_ok=True)
    made_change = False

    with open(args.log_path, "w", encoding="utf-8") as log:
        for _ in range(15):
            prompt = "\n\n".join(m["content"] for m in messages)

            response = model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.2,
