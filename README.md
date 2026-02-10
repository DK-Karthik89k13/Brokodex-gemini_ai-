# Brokodex-gemini_ai-

# File Structure :
SWE-bench-Pro-Evaluation/
│
├── .github/
│   └── workflows/
│       └── swebench-gemini.yml        # Your GitHub Actions workflow
│
├── scripts/
│   ├── run_agent.py                   # Main script to run the Gemini agent
│   └── tools.py
|
└──artifacts/                         # Created at runtime to store outputs
    ├── pre_validation.log
    ├── post_validation.log
    ├── prompts.md
    ├── results.json
    └── changes.patch
