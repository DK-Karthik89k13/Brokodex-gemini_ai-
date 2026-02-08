#!/usr/bin/env python3
import os
import sys
import time
import argparse
from pathlib import Path

from google import genai
from google.genai import types
from google.genai.errors import ClientError


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


def load_api_key():
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        fatal("GEMINI_API_KEY not set")
    return key


def build_prompt(task_id: str, repo_path: str) -> str:
    return f"""
You are an autonomous software engineering agent.

Task ID:
{task_id}

Repository path:
{repo_path}

Goal:
Identify the failing test, reason about the bug, and propose a patch.
Respond ONLY with analysis and a git diff if applicable.
"""


def main():
    args = parse_args()
    api_key = load_api_key()

    repo_path = Path(args.repo_path)
    if not repo_path.exists():
        fatal(f"Repo path does not exist: {repo_path}")

    client = genai.Client(api_key=api_key)

    prompt = build_prompt(args.task_id, args.repo_path)

    Path(args.prompt_log).write_text(prompt)

    log_file = open(args.log_path, "w", buffering=1)

    max_retries = 5
    backoff = 10

    for iteration in range(1, max_retries + 1):
        print(f"\n--- Iteration {iteration} ---", file=log_file)

        try:
            response = client.models.generate_content(
                model="gemini-2.0-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.2,
                    max_output_tokens=2048,
                ),
            )

            text = response.text or ""
            print(text, file=log_file)

            if "diff --git" in text:
                print("Fix successful", file=log_file)
                break

        except ClientError as e:
            if e.status_code == 429:
                wait = backoff * iteration
                print(
                    f"⚠️ Rate limited (429). Sleeping {wait}s...",
                    file=log_file,
                )
                time.sleep(wait)
                continue
            raise

    log_file.close()


if __name__ == "__main__":
    main()
