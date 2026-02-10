
import os
import sys
import json
import time
import subprocess
import re
import yaml
from datetime import datetime, timezone

# Configuration
API_KEY = os.environ.get("GEMINI_API_KEY")
MODELS = [
    "claude-3-5-sonnet-latest",
    "claude-3-5-haiku-latest",
]
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
TASK_FILE = os.path.join(SCRIPT_DIR, "task.yaml")
ARTIFACTS_DIR = os.getcwd()

def log_event(event_type, content, **kwargs):
    """Log event to agent.log in JSONL format."""
    log_file = os.path.join(ARTIFACTS_DIR, "agent.log")
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        "type": event_type,
        "content": content
    }
    entry.update(kwargs)
    with open(log_file, "a") as f:
        f.write(json.dumps(entry) + "\n")

def run_command(command, cwd=None, log_file=None):
    """Execute a bash command and return its output."""
    print(f"Executing: {command}")
    try:
        env = os.environ.copy()
        env["PYTHONPATH"] = f"/testbed:/testbed/vendor/infogami:{env.get('PYTHONPATH', '')}"
        
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=cwd,
            env=env
        )
        output = result.stdout + result.stderr
        
        if log_file:
            with open(os.path.join(ARTIFACTS_DIR, log_file), "w") as f:
                f.write(output)
        
        return result.returncode, output
    except Exception as e:
        return -1, str(e)

def call_claude(system_prompt, user_message):
    """Call Anthropic API using the official SDK for reliable communication."""
    from anthropic import Anthropic
    
    if not API_KEY:
        print("Error: ANTHROPIC_API_KEY environment variable is missing.")
        return None

    client = Anthropic(api_key=API_KEY)
    
    for model in MODELS:
        log_event("request", user_message, model=model)
        
        try:
            print(f"Calling Claude with model {model}...")
            message = client.messages.create(
                model=model,
                max_tokens=4096,
                system=system_prompt,
                messages=[{"role": "user", "content": user_message}]
            )
            
            content = message.content[0].text
            usage = {
                "input_tokens": message.usage.input_tokens,
                "output_tokens": message.usage.output_tokens
            }
            
            log_event("response", content, model=model, usage=usage)
            return content
        except Exception as e:
            print(f"Error with model {model}: {e}")
            continue
    
    return None

def extract_patch(text):
    """Extract git patch from markdown blocks."""
    match = re.search(r"```diff\n(.*?)\n```", text, re.DOTALL)
    if match:
        return match.group(1)
    match = re.search(r"```\n(diff --git.*?)\n```", text, re.DOTALL)
    if match:
        return match.group(1)
    # If no block, look for diff --git directly
    if "diff --git" in text:
        start = text.find("diff --git")
        return text[start:]
    return None

def main():
    if not API_KEY:
        print("Error: ANTHROPIC_API_KEY environment variable not set.", file=sys.stderr)
        print("When running in GitHub Actions, this environment variable is populated from the 'CLAUDE_API_KEY' secret.", file=sys.stderr)
        print("\nTo fix this issue:", file=sys.stderr)
        print("1. Add your Anthropic API key as a GitHub repository secret named 'CLAUDE_API_KEY'", file=sys.stderr)
        print("2. Go to: Settings > Secrets and variables > Actions > New repository secret", file=sys.stderr)
        print("3. Name: CLAUDE_API_KEY", file=sys.stderr)
        print("4. Value: Your Anthropic API key (starts with 'sk-ant-')", file=sys.stderr)
        sys.exit(1)

    # 1. Load Task
    if not os.path.exists(TASK_FILE):
        print(f"Error: {TASK_FILE} not found.")
        sys.exit(1)
        
    with open(TASK_FILE, 'r') as f:
        task = yaml.safe_load(f)

    print(f"=== Task: {task['title']} ===")
    
    # 2. Pre-verification
    print("Running pre-verification tests...")
    test_cmd = task['tests']['test_command']
    rc, pre_output = run_command(test_cmd, log_file="pre_verification.log")
    print(f"Pre-verification exit code: {rc}")

    # 3. Use AI Agent to generate fix
    system_prompt = f"""You are an expert software engineer. Fix the following issue in the OpenLibrary repository.
Title: {task['title']}
Description: {task['description']}

Technical Requirements:
{task.get('requirements', 'N/A')}

Interface Specification:
{task.get('interface', 'N/A')}

Files to modify: {', '.join(task.get('files_to_modify', []))}

Current Working Directory: /testbed
You must provide your fix as a git patch in a ```diff block.
Ensure the patch can be applied with `git apply`.
"""

    user_message = f"Pre-verification tests failed with the following output:\n\n{pre_output}\n\nPlease provide a fix."
    
    # Document prompt
    with open(os.path.join(ARTIFACTS_DIR, "prompts.md"), "w") as f:
        f.write("# Prompts Used\n\n")
        f.write("## System Prompt\n\n")
        f.write(f"```\n{system_prompt}\n```\n\n")
        f.write("## User Message\n\n")
        f.write(f"```\n{user_message}\n```\n")

    response = call_claude(system_prompt, user_message)
    if not response:
        print("Failed to get response from Claude.")
        sys.exit(1)

    # 4. Apply Fix
    patch = extract_patch(response)
    if not patch:
        print("No patch found in Claude's response.")
        # Try to use the whole response if it starts with diff
        if response.strip().startswith("diff"):
            patch = response.strip()
        else:
            sys.exit(1)

    log_event("tool_use", "Applying generated patch", tool="git_apply", args={"patch_length": len(patch)})
    patch_path = os.path.join(ARTIFACTS_DIR, "changes.patch")
    with open(patch_path, "w") as f:
        f.write(patch)
    
    print("Applying patch...")
    rc_apply, apply_out = run_command(f"git apply {patch_path}", cwd="/testbed")
    if rc_apply != 0:
        print(f"Git apply failed: {apply_out}")
        print("Trying with -p1...")
        rc_apply, apply_out = run_command(f"patch -p1 < {patch_path}", cwd="/testbed")
        if rc_apply != 0:
            print(f"Patch apply failed: {apply_out}")
            # We continue anyway to see if post-verification somehow passes or to have logs

    # 5. Post-verification
    print("Running post-verification tests...")
    rc_post, post_output = run_command(test_cmd, log_file="post_verification.log")
    print(f"Post-verification exit code: {rc_post}")
    
    if rc_post == 0:
        print("SUCCESS: Tests passed after applying the fix.")
    else:
        print("FAILURE: Tests still failing after applying the fix.")

if __name__ == "__main__":
    main()
