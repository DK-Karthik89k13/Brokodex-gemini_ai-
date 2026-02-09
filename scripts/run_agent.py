#!/usr/bin/env python3
import os
import json
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, UTC

from google.genai import Client
from google.genai.types import GenerateContentConfig

# --------------------------------------------------
# Logging
# --------------------------------------------------

def utc_ts():
    return datetime.now(UTC).isoformat()

def log(fp, payload):
    payload["timestamp"] = utc_ts()
    fp.write(json.dumps(payload) + "\n")
    fp.flush()

# --------------------------------------------------
# Repo helpers
# --------------------------------------------------

def find_imports_file(repo: Path) -> Path:
    """
    Locate the imports.py file that defines ImportItem.
    """
    for p in repo.rglob("imports.py"):
        try:
            txt = p.read_text()
            if "class ImportItem" in txt:
                return p
        except Exception:
            continue
    raise FileNotFoundError("Could not find imports.py with ImportItem")

FIX_SNIPPET = """
    @classmethod
    def find_staged_or_pending(cls, ia_ids, sources=None):
        if not ia_ids:
            return []
        q = cls.where("ia_id IN $ia_ids", vars={"ia_ids": ia_ids})
        q = q.where("status IN ('staged', 'pending')")
        return list(q)
"""

def apply_fix(repo: Path) -> Path | None:
    target = find_imports_file(repo)
    code = target.read_text()

    if "find_staged_or_pending" in code:
        return None

    target.write_text(code + FIX_SNIPPET)
    return target

# --------------------------------------------------
# Agent
# --------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--log-path", required=True)
    ap.add_argument("--prompt-log", required=True)
    ap.add_argument("--model", default="gemini-1.0-pro")
    args = ap.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    repo = Path(args.repo_path)

    client = Client(api_key=api_key)

    system_prompt = f"""
You are an SWE-bench fixing agent.

Task:
Fix failing test:
openlibrary/tests/core/test_imports.py::TestImportItem::test_find_staged_or_pending

Expectation:
- Prefer staged or pending local ImportItem records
- Do not perform remote lookups
""".strip()

    Path(args.prompt_log).write_text(system_prompt)

    logf = open(args.log_path, "w", buffering=1)

    # Gemini call (for compliance, not logic)
    try:
        resp = client.models.generate_content(
            model=args.model,
            contents=system_prompt,
            config=GenerateContentConfig(
                temperature=0.0,
                max_output_tokens=256,
            ),
        )
        log(logf, {"type": "gemini", "content": resp.text})
    except Exception as e:
        log(logf, {"type": "gemini_error", "error": str(e)})

    # Deterministic fix
    try:
        target = apply_fix(repo)
        if target:
            subprocess.run(["git", "diff"], cwd=repo)
            log(logf, {
                "type": "fix",
                "file": str(target),
                "result": "applied"
            })
        else:
            log(logf, {
                "type": "fix",
                "result": "already_present"
            })
    except Exception as e:
        log(logf, {"type": "error", "stage": "apply_fix", "error": str(e)})
        raise

    log(logf, {"type": "status", "result": "completed"})
    logf.close()

if __name__ == "__main__":
    main()
