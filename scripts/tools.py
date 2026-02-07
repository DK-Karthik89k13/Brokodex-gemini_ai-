import subprocess
from pathlib import Path


def read_file(path: str) -> str:
    return Path(path).read_text()


def write_file(path: str, content: str) -> str:
    Path(path).write_text(content)
    return "OK"


def edit_file(path: str, old: str, new: str) -> str:
    p = Path(path)
    text = p.read_text()
    if old not in text:
        raise ValueError("Old content not found")
    p.write_text(text.replace(old, new, 1))
    return "OK"


def run_bash(command: str) -> str:
    result = subprocess.run(
        command,
        shell=True,
        text=True,
        capture_output=True,
    )
    return result.stdout + result.stderr
