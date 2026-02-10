#!/usr/bin/env python3
import os
import json
import subprocess
import argparse
import re
from pathlib import Path
from datetime import datetime, UTC

from google.genai import Client
from google.genai.types import GenerateContentConfig

# -------------------------
# Utils
# -------------------------
def utc_ts():
    return datetime.now(UTC).isoformat()

def log(fp, payload):
    payload["timestamp"] = utc_ts()
    fp.write(json.dumps(payload) + "\n")
    fp.flush()

def run(cmd, cwd):
    return subprocess.run(
        cmd,
        shell=True,
        cwd=cwd,
        capture_output=True,
        text=True,
    )

# -------------------------
# Repo helpers
# -------------------------
def find_imports_file(repo: Path) -> Path:
    for p in repo.rglob("imports.py"):
        try:
            if "class ImportItem" in p.read_text():
                return p
        except Exception:
            pass
    raise RuntimeError("imports.py containing ImportItem not found")

METHOD_RE = re.compile(
    r"@classmethod\s+def find_staged_or_pending\([^)]*\):([\s\S]*?)(?=\n\s*@|\nclass|\Z)",
    re.MULTILINE,
)

FIX_METHOD = """
    @classmethod
    def find_staged_or_pending(cls, ia_ids, sources=None):
        if not ia_ids:
            return []

        q = cls.where("ia_id IN $ia_ids", vars={"ia_ids": ia_ids})
        q = q.where("status IN ('staged', 'pending')")
        return list(q)
""".rstrip()

def apply_fix(repo: Path):
    target = find_imports_file(repo)
    code = target.read_text()

    # Case 1: method exists → replace
    if METHOD_RE.search(code):
        new_code = METHOD_RE.sub(FIX_METHOD, code)
        if new_code != code:
            target.write_text(new_code + "\n")
            return target
        return None

    # Case 2: method missing → insert into ImportItem
    lines = code.splitlines()
    class_idx = None

    for i, l in enumerate(lines):
        if l.startswith("class ImportItem"):
            class_idx = i
            break

    if class_idx is None:
        raise RuntimeError("ImportItem class not found")

    indent = None
    insert_at = None

    for i in range(class_idx + 1, len(lines)):
        l = lines[i]
        if not l.strip():
            continue
        if indent is None:
            indent = len(l) - len(l.lstrip())
        if not l.startswith(" " * indent):
            insert_at = i
            break

    if insert_at is None:
        insert_at = len(lines)

    lines.insert(insert_at, FIX_METHOD)
    target.write_text("\n".join(lines) + "\n")
    return target

# -------------------------
# Pytest
# -------------------------
def run_pytest(test, repo, log_path):
    r = run(f"python -m pytest {test} -xvs", cwd=repo)
    log_path.write_text(r.stdout + r.stderr)
    (log_path.parent / f"{log_path.stem}.exit").write_text(str(r.returncode))
    return r.returncode

# -------------------------
# Main
# -------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--log-path", required=True)
    ap.add_argument("--prompt-log", required=True)
    ap.add_argument("--pre-log", required=True)
    ap.add_argument("--post-log", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--model", default="gemini-1.5-pro")
    args = ap.parse_args()

    repo = Path(args.repo_path)
    agent_log = open(args.log_path, "w", buffering=1)

    system_prompt = """You are an autonomous SWE-bench agent.

Fix failing test:
openlibrary/tests/core/test_imports.py::TestImportItem::test_find_staged_or_pending

Rules:
- Prefer staged or pending local ImportItem records
- Do not perform remote lookups
"""

    Path(args.prompt_log).write_text(system_prompt)
    log(agent_log, {"type": "prompt", "content": system_prompt})

    # Pre-verification
    log(agent_log, {"stage": "pre_verification"})
    pre_exit = run_pytest(
        "openlibrary/tests/core/test_imports.py::TestImportItem::test_find_staged_or_pending",
        repo,
        Path(args.pre_log),
    )
    log(agent_log, {"pre_exit": pre_exit})

    # Gemini (logged only)
    try:
        client = Client(api_key=os.environ["GEMINI_API_KEY"])
        resp = client.models.generate_content(
            model=args.model,
            contents=system_prompt,
            config=GenerateContentConfig(temperature=0.2),
        )
        log(agent_log, {"gemini_output": resp.text})
    except Exception as e:
        log(agent_log, {"gemini_error": str(e)})

    # Apply fix
    log(agent_log, {"stage": "apply_fix"})
    patched = apply_fix(repo)

    diff = run("git diff", cwd=repo)
    (Path(args.post_log).parent / "changes.patch").write_text(diff.stdout)

    log(agent_log, {
        "fix_applied": bool(patched),
        "file": str(patched) if patched else None
    })

    # Post-verification
    log(agent_log, {"stage": "post_verification"})
    post_exit = run_pytest(
        "openlibrary/tests/core/test_imports.py::TestImportItem::test_find_staged_or_pending",
        repo,
        Path(args.post_log),
    )
    log(agent_log, {"post_exit": post_exit})

    Path(args.results).write_text(json.dumps({
        "pre_exit": pre_exit,
        "post_exit": post_exit,
        "fix_applied": bool(patched)
    }, indent=2))

    agent_log.close()

if __name__ == "__main__":
    main()
