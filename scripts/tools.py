import os
import subprocess
import re

# We hardcode or pass the repo root to ensure the agent 
# never accidentally edits files outside the testbed.
REPO_ROOT = "/testbed"

def read_file(path):
    full_path = os.path.join(REPO_ROOT, path)
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {e}"

def write_file(path, content):
    full_path = os.path.join(REPO_ROOT, path)
    try:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Successfully wrote to {path}"
    except Exception as e:
        return f"Error writing file: {e}"

def edit_file(path, old, new):
    """
    Search and replace tool. Using exact match to prevent 
    the agent from hallucinating nearby code.
    """
    full_path = os.path.join(REPO_ROOT, path)
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if old not in content:
            return f"Error: Could not find exact match for the 'old' block in {path}"
        
        new_content = content.replace(old, new)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return f"Successfully edited {path}"
    except Exception as e:
        return f"Error editing file: {e}"

def run_bash(command):
    """
    Runs commands strictly within the repo root.
    """
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            timeout=60  # Prevent infinite loops in tests
        )
        return f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}\nEXIT CODE: {result.returncode}"
    except subprocess.TimeoutExpired:
        return "Error: Command timed out after 60 seconds."
    except Exception as e:
        return f"Error running bash: {e}"
