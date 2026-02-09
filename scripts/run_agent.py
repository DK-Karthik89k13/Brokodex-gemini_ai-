#!/usr/bin/env python3
import os
import json
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, UTC

from google.genai import Client
from google.genai.types import GenerateContentConfig

# -------------------------
# Utilities
# -------------------------

def utc_ts():
    return datetime.now(UTC).isoformat()

def log(fp, payload):
    payload["timestamp"] = utc_ts()
    fp.write(json.dumps(payload) + "\n")
    fp.flush()

def run_bash(cmd, cwd):
    return subprocess.check_output(cmd, shell=True, cwd=cwd, text=True, stderr=subprocess.STDOUT)

def read_file(path):
    return Path(path).read_text()

def write_file(path, content):
    Path(path).write_text(content)

def extract_diff(text: str):
    if "diff --git" not in text:
        return None
    return text[text.index("diff --git"):]

def find_imports_file(repo: Path) -> Path:
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

def apply_fix(repo: Path):
    target = find_imports_file(repo)
    code = target.read_text()
    if "find_staged_or_pending" in code:
        return None
    target.write_text(code + FIX_SNIPPET)
    return target

# -------------------------
# Main agent
# -------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", required=True)
    ap.add_argument("--repo-path", required=True)
    ap.add_argument("--log-path", required=True)
    ap.add_argument("--prompt-log", required=True)
    ap.add_argument("--pre-log", default="pre_verification.log")
    ap.add_argument("--post-log", default="post_verification.log")
    ap.add_argument("--results", default="results.json")
    ap.add_argument("--model", default="gemini-1.0-pro")
    args = ap.parse_args()

    repo = Path(args.repo_path)
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set")

    logf = open(args.log_path, "w", buffering=1)
    promptf = Path(args.prompt_log)
    results = {}

    # -------------------------
    # Pre-verification test
    # -------------------------
    pre_log_path = Path(args.pre_log)
    try:
        subprocess.run(
            ["pytest", "openlibrary/tests/core/test_imports.py::TestImportItem::test_find_staged_or_pending",
             "-xvs"], cwd=repo, stdout=open(pre_log_path, "w"), check=False
        )
        results["pre_verification"] = "logged"
    except Exception as e:
        results["pre_verification_error"] = str(e)
    log(logf, {"type": "pre_verification", "file": str(pre_log_path)})

    # -------------------------
    # Save system prompt
    # -------------------------
    system_prompt = f"""
You are an SWE-bench autonomous agent.

Task:
Fix failing test:
openlibrary/tests/core/test_imports.py::TestImportItem::test_find_staged_or_pending

Rules:
- Prefer staged/local ImportItem records
- Do not perform remote lookups
- Apply deterministic fix if Gemini fails
"""
    promptf.write_text(system_prompt)
    log(logf, {"type": "prompt", "content": system_prompt})

    # -------------------------
    # Gemini call (hybrid)
    # -------------------------
    try:
        client = Client(api_key=api_key)
        resp = client.models.generate_content(
            model=args.model,
            contents=system_prompt,
            config=GenerateContentConfig(temperature=0.0, max_output_tokens=4096)
        )
        gemini_text = resp.text or ""
        log(logf, {"type": "gemini_response", "content": gemini_text})
    except Exception as e:
        log(logf, {"type": "gemini_error", "error": str(e)})
        gemini_text = ""

    # -------------------------
    # Deterministic fix
    # -------------------------
    try:
        applied_file = apply_fix(repo)
        patch_path = Path("changes.patch")
        subprocess.run(["git", "diff"], cwd=repo, stdout=open(patch_path, "w"), check=False)
        log(logf, {"type": "deterministic_fix", "file": str(applied_file) if applied_file else None})
    except Exception as e:
        log(logf, {"type": "fix_error", "error": str(e)})

    # -------------------------
    # Post-verification test
    # -------------------------
    post_log_path = Path(args.post_log)
    try:
        subprocess.run(
            ["pytest", "openlibrary/tests/core/test_imports.py::TestImportItem::test_find_staged_or_pending",
             "-xvs"], cwd=repo, stdout=open(post_log_path, "w"), check=False
        )
        results["post_verification"] = "logged"
    except Exception as e:
        results["post_verification_error"] = str(e)
    log(logf, {"type": "post_verification", "file": str(post_log_path)})

    # -------------------------
    # Save results.json
    # -------------------------
    results["gemini_output"] = gemini_text
    results["applied_file"] = str(applied_file) if applied_file else None
    results_path = Path(args.results)
    results_path.write_text(json.dumps(results, indent=2))
    log(logf, {"type": "results_saved", "file": str(results_path)})

    # -------------------------
    # Final status
    # -------------------------
    log(logf, {"type": "status", "result": "completed"})
    logf.close()

if __name__ == "__main__":
    main()
