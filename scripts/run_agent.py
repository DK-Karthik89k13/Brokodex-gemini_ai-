import os
import json
import argparse
import subprocess
import google.generativeai as genai
from tools import read_file, write_file, edit_file, run_bash

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

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--repo-path", required=True)
    parser.add_argument("--log-path", required=True)
    parser.add_argument("--prompt-log", required=True)
    args = parser.parse_args()

    # Get the API Key from environment
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
        print("✅ Test already passes — invalid task")
        return

    # Gemini uses a ChatSession to manage history easily
    chat = model.start_chat(history=[])
    
    system_instruction = (
        "You are a senior engineer fixing a failing test.\n"
        "Stop ONLY after the test passes.\n\n"
        + TOOL_INSTRUCTIONS
    )

    initial_prompt = (
        f"{system_instruction}\n\n"
        f"Repository path: {args.repo_path}\n\n"
        f"Failing test:\n{test_cmd}\n\n"
        f"Failure output:\n{result.stdout}\n{result.stderr}"
    )

    os.makedirs(os.path.dirname(args.log_path), exist_ok=True)

    # Simple loop for the agent's thought/act cycle
    current_prompt = initial_prompt
    for i in range(15):
        print(f"--- Iteration {i+1} ---")
        response = chat.send_message(current_prompt)
        response_text = response.text.strip()
        
        # Log the response
        with open(args.log_path, "a", encoding="utf-8") as log:
            log.write(f"\nIteration {i+1} Response:\n{response_text}\n")

        try:
            # Basic cleanup in case Gemini wraps JSON in markdown blocks
            clean_json = response_text.replace("```json", "").replace("```", "").strip()
            action = json.loads(clean_json)
            
            tool_name = action.get("tool")
            tool_args = action.get("args", {})

            if tool_name in TOOLS:
                print(f"Executing {tool_name}...")
                tool_result = TOOLS[tool_name](**tool_args)
                current_prompt = f"Tool result:\n{tool_result}"
            else:
                current_prompt = f"Error: Tool {tool_name} not found."
        
        except Exception as e:
            current_prompt = f"Error parsing JSON or executing tool: {str(e)}"

        # Check if the test passes after the fix
        if run(test_cmd, args.repo_path).returncode == 0:
            print("✅ Fix successful! Test passes.")
            break

if __name__ == "__main__":
    main()
