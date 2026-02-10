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
    

## Workflow

1. **Workflow Trigger**  
   Triggered manually via GitHub Actions (`workflow_dispatch`) with a `task_id` input.

2. **Environment Setup**  
   - Ubuntu container with Python 3.12
   - Installs system dependencies (`build-essential`, `libpq-dev`)
   - Installs Python dependencies (`pytest`, `web.py`, `Cython`, `google-generativeai`, etc.)

3. **Repository Preparation**  
   - Clones OpenLibrary into `/testbed`
   - Checks out commit extracted from `task_id`

4. **Infogami Setup**  
   - Clones Infogami if not present
   - Installs in editable mode
   - Sanity check import

5. **Run Gemini Agent**  
   - Executes `scripts/run_agent.py` with:
     - `--repo-path /testbed`
     - Logs: pre/post validation, prompts, results
     - Optional task file if provided

6. **Post-Processing**  
   - Captures code changes as `changes.patch`
   - Uploads all artifacts to GitHub for review

## Artifacts

- `pre_validation.log` → Initial repo validation logs
- `post_validation.log` → Validation after agent execution
- `prompts.md` → Prompts used for Gemini agent
- `results.json` → Task evaluation results
- `changes.patch` → Git diff after agent modifications

## Usage

1. Set up `GEMINI_API_KEY` as a secret in your GitHub repository.
2. Trigger the workflow manually and provide a `task_id`.
3. Review uploaded artifacts after completion.

## Notes

- The workflow is containerized to ensure consistent environment.
- Python dependencies are installed inside `/testbed` to isolate the repo.
- Idempotent installation ensures Infogami can be reused without repeated cloning.
