#!/usr/bin/env python3
import os
import json
import argparse
import subprocess
import datetime
from pathlib import Path

import google.generativeai as genai
from google.generativeai.types import FunctionDeclaration, Tool

# ------------------------
# Utilities
# ------------------------

def ts():
    return datetime.datetime.utcnow().isoformat() + "Z"

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

def edit_file(path, diff):
    p = Path("/tmp/agent.patch")
    p.write_text(diff)
    run_bash(f"git apply {p}", cwd="/")
    return "patched"

# ------------------------
# Tool schema
# ------------------------

tools = Tool(
    function_declarations=[
        FunctionDeclaration(
            name="read_file",
            description="Read a file from disk",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"}
                },
                "required": ["path"]
            },
        ),
        FunctionDeclaration(
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
        FunctionDeclaration(
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

# ------------------------
# Agent loop
# ------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--log-path", required=True)
    ap.add_argument("--prompt-log", required=True)
    ap.add_argument("--model", default="gemini-1.5-flash",
                    choices=["gemini-1.5-flash", "gemini-1.5-pro"])
    args = ap.parse_args()

    if "GEMINI_API_KEY" not in os.environ:
        raise RuntimeError("GEMINI_API_KEY not set")

    genai.configure(api_key=os.environ["GEMINI_API_KEY"])

    model = genai.GenerativeModel(
        model_name=args.model,
        tools=tools,
    )

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
- When done, STOP
"""

    Path(args.prompt_log).write_text(system_prompt)

    chat = model.start_chat(history=[
        {"role": "user", "parts": [system_prompt]}
    ])

    logf = open(args.log_path, "w")

    for step in range(1, 15):
        log(logf, {"type": "iteration", "step": step})

        response = chat.send_message("Proceed")

        for cand in response.candidates:
            for part in cand.content.parts:
                # ---- Tool call ----
                if hasattr(part, "function_call"):
                    fn = part.function_call.name
                    argsd = dict(part.function_call.args)

                    log(logf, {
                        "type": "tool_use",
                        "tool": fn,
                        "args": argsd,
                    })

                    if fn == "read_file":
                        out = read_file(argsd["path"])
                    elif fn == "write_file":
                        out = write_file(argsd["path"], argsd["content"])
                    elif fn == "run_bash":
                        out = run_bash(argsd["cmd"], argsd["cwd"])
                    else:
                        out = "unknown tool"

                    chat.send_message({
                        "role": "tool",
                        "name": fn,
                        "parts": [out],
                    })

                # ---- Final text ----
                elif hasattr(part, "text"):
                    text = part.text
                    log(logf, {"type": "response", "content": text})

                    if "diff --git" in text:
                        patch = Path("/tmp/final.patch")
                        patch.write_text(text)
                        run_bash(
                            f"git apply {patch}",
                            cwd=args.repo_path
                        )
                        log(logf, {
                            "type": "status",
                            "result": "Fix applied"
                        })
                        logf.close()
                        return

    logf.close()
    raise RuntimeError("Agent failed to converge")

if __name__ == "__main__":
    main()
