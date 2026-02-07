import os
import json
import argparse
import subprocess
from google import genai

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
- Use tools to fix the failing test.
- Paths should be relative to the repository root.
- Do NOT explain anything.
- Do NOT add text outside JSON.
"""


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

    client = genai.Client(api_key=api_key)

    test_cmd = (
        "python -m pytest "
        "openlibrary/tests/core/test_imports.py::TestImportItem::test_find_staged_or_pending -xvs"
    )

    # Pre-check
    result = run(test_cmd, args.repo_path)
    if result.returncode == 0:
        print("✅ Test already passes — invalid task")
        return

    chat = client.chats.create(
        model="gemini-1.5-flash",
        history=[]
    )

    system_instruction = (
        "You are a senior engineer fixing a failing test.\n"
        f"The repository is located at {args.repo_path}\n"
        "Stop ONLY after the test passes.\n\n"
        + TOOL_INSTRUCTIONS
    )

    current_prompt = (
        f"{system_instruction}\n\n"
        f"Failing test output:\n{result.stdout}\n{result.stderr}"
    )

    os.makedirs(os.path.dirname(args.log_path), exist_ok=True)

    for i in range(15):
        print(f"--- Iteration {i + 1} ---")

        response = chat.send_message(current_prompt)
        response_text = response.candidates[0].content.parts[0].text.strip()

        with open(args.log_path, "a", encoding="utf-8") as log:
            log.write(f"\n--- Iteration {i + 1} ---\n{response_text}\n")

        try:
            clean_json = response_text
            if "```json" in clean_json:
                clean_json = clean_json.split("```json")[1].split("```")[0]
            elif "```" in clean_json:
                clean_json = clean_json.split("```")[1].split("```")[0]

            action = json.loads(clean_json.strip())
            tool_name = action.get("tool")
            tool_args = action.get("args", {})

            if tool_name in TOOLS:
                tool_result = TOOLS[tool_name](**tool_args)
                current_prompt = f"Tool result:\n{tool_result}"
            else:
                current_prompt = f"Error: Tool {tool_name} not found."

        except Exception as e:
            current_prompt = f"Error parsing JSON or executing tool: {str(e)}"

        if run(test_cmd, args.repo_path).returncode == 0:
            with open(args.log_path, "a") as log:
                log.write("\nFix successful!")
            print("✅ Fix successful! Test passes.")
            break


if __name__ == "__main__":
    main()
