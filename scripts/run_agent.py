#!/usr/bin/env python3
import os
import sys
import time
import argparse
from pathlib import Path

import vertexai
from vertexai.preview.generative_models import GenerativeModel
from google.api_core.exceptions import ResourceExhausted, GoogleAPIError


# ---------------- utils ----------------
def fatal(msg: str):
    print(f"\n❌ {msg}", file=sys.stderr)
    sys.exit(1)


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--task-id", required=True)
    p.add_argument("--repo-path", required=True)
    p.add_argument("--log-path", required=True)
    p.add_argument("--prompt-log", required=True)
    return p.parse_args()


# ---------------- auth ----------------
def init_vertex():
    project = os.environ.get("GCP_PROJECT")
    region = os.environ.get("GCP_REGION", "us-central1")

    if not project:
        fatal("GCP_PROJECT env var not set")

    vertexai.init(project=project, location=region)


# ---------------- prompt ----------------
def build_prompt(task_id: str, repo_path: str) -> str:
    return f"""
You are an autonomous senior Python engineer.

Task ID:
{task_id}

Repository path:
{repo_path}

RULES:
- Identify why the test is failing
- Propose a FIX
- Output ONLY a unified git diff
- No explanations, no markdown, no extra text

Target test:
openlibrary/tests/core/test_imports.py::TestImportItem::test_find_staged_or_pending
"""


# ---------------- main ----------------
def main():
    args = parse_args()

    repo_path = Path(args.repo_path)
    if not repo_path.exists():
        fatal(f"Repo path does not exist: {repo_path}")

    init_vertex()

    model = GenerativeModel("gemma-3-27b-it")

    prompt = build_prompt(args.task_id, args.repo_path)
    Path(args.prompt_log).write_text(prompt)

    log_file = open(args.log_path, "w", buffering=1)

    max_iters = 5
    backoff = 20

    for i in range(1, max_iters + 1):
        print(f"\n--- Iteration {i} ---", file=log_file)

        try:
            response = model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.2,
                    "max_output_tokens": 2048,
                },
            )

            text = response.text or ""
            print(text, file=log_file)

            if "diff --git" in text:
                print("\nFix successful", file=log_file)
                break

        except ResourceExhausted:
            wait = backoff * i
            print(f"⚠️ Rate limited. Sleeping {wait}s...", file=log_file)
            time.sleep(wait)

        except GoogleAPIError as e:
            fatal(f"Vertex AI error: {e}")

    log_file.close()


if __name__ == "__main__":
    main()
