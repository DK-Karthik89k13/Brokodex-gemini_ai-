#!/usr/bin/env python3
import os
import json
import argparse
import subprocess
import datetime
from pathlib import Path

from google import genai
from google.genai import types

# ------------------------
# Utilities
# ------------------------

def ts():
    return datetime.datetime.now(datetime.UTC).isoformat()

def log(fp, payload):
    payload["timestamp"] = ts()
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

# ------------------------
# Tool definitions (NEW SDK)
# ------------------------

TOOLS = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="read_file",
                description="Read a file from disk",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"}
                    },
                    "required": ["path"],
                },
            ),
            types.FunctionDeclaration(
                name="write_file",
                description="Write content to a file",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            ),
            types.FunctionDeclaration(
                name="run_bash",
                description="Run a bash command",
                parameters={
                    "type": "object",
                    "properties": {
                        "cmd": {"type": "string"},
                        "cwd": {"type": "string"},
                    },
                    "required": ["cmd", "cwd"],
                },
            ),
        ]
    )
]

# ------------------------
# Agent
# ------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--log-path", required=True)
    ap.add_argument("--prompt-log", required=True)
    ap.add_argument("--model", default="gemini-1.5-pro")
    args = ap.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=api_key)

    system_prompt = f"""
You are an autonomous SWE-bench coding agent.

Task ID:
{args.task_id}

Repository path:
{args.repo_path}

Rules:
- Fix failing test: TestImportItem::test_find_staged_or_pending
- Prefer local staged ISBN records
- Use tools to inspect and modify code
- Output a unified git diff when done
"""

    Path(args.prompt_log).write_text(system_prompt)
    logf = open(args.log_path, "w")

    conversation = system_prompt

    for step in range(1, 12):
        log(logf, {"type": "iteration", "step": step})

        response = client.models.generate_content(
            model=args.model,
            contents=conversation,
            tools=TOOLS,
        )

        for part in response.candidates[0].content.parts:
            # Tool call
            if part.function_call:
                fn = part.function_call.name
                argsd = dict(part.function_call.args)

                log(logf, {"type": "tool_use", "tool": fn, "args": argsd})

                if fn == "read_file":
                    result = read_file(argsd["path"])
                elif fn == "write_file":
                    result = write_file(argsd["path"], argsd["content"])
                elif fn == "run_bash":
                    result = run_bash(argsd["cmd"], argsd["cwd"])
                else:
                    result = "unknown tool"

                conversation += f"\nTool {fn} result:\n{result}\n"

            # Text output
            elif part.text:
                text = part.text
                log(logf, {"type": "response", "content": text})
                conversation += "\n" + text

                if "diff --git" in text:
                    patch = Path("/tmp/final.patch")
                    patch.write_text(text)
                    run_bash(f"git apply {patch}", cwd=args.repo_path)
                    log(logf, {"type": "status", "result": "Patch applied"})
                    logf.close()
                    return

    logf.close()
    raise RuntimeError("Agent failed to converge")

if __name__ == "__main__":
    main()

