# SWE-bench Pro Evaluation (Gemini)

## Project Overview
SWE-bench Pro Evaluation is an automated framework for assessing and correcting issues in the OpenLibrary repository using the Gemini AI agent. The system runs pre-validation tests, attempts to auto-fix broken modules, runs post-validation tests, and captures all results, logs, and code changes for review.

This workflow is designed to:
- Validate the OpenLibrary codebase for Python import issues and test failures.
- Automatically reinstall missing modules.
- Track all changes, logs, and prompts.
- Generate HTML and JSON reports summarizing the evaluation.

---

## Repository Structure

## Repository Structure

📁 SWE-bench-Pro-Evaluation
├── 📁 .github
│   └── 📁 workflows
│       └── 📄 swebench-gemini.yml  – GitHub Actions workflow for running the Gemini agent
├── 📁 scripts
│   └── 📄 run_agent.py              – Core script executing Gemini evaluation
├── 📁 tools
│   └── 📄 tools.py                  – Helper utilities for file operations and subprocess calls
├── 📁 testbed                       – OpenLibrary repository cloned at a specific commit
│   └── …                            – Contains all OpenLibrary source code
├── 📁 infogami                      – Optional Infogami repo cloned/installed for testing
│   └── …                            – Infogami source files
├── 📁 artifacts                     – Generated outputs during evaluation
│   ├── 📄 pre_validation.log        – Logs from pre-validation tests
│   ├── 📄 post_validation.log       – Logs from post-validation tests
│   ├── 📄 prompts.md                – Record of prompts sent to the Gemini agent
│   ├── 📄 results.json              – JSON summary of test results and changes
│   └── 📄 changes.patch             – Git diff of changes applied during evaluation
├── 📄 extract_metrics.py            – Script to extract metrics for reporting
├── 📄 setup_repository.sh           – Helper script to setup the repository
├── 📄 pyproject.toml                – Python project metadata and dependencies
└── 📄 task.yaml                     – Task definitions for evaluation




---

## Workflow Overview

1. **Workflow Trigger**  
   The evaluation is triggered manually via GitHub Actions with a `task_id` input, which determines the commit to test in OpenLibrary.

2. **Environment Setup**  
   - Runs in an Ubuntu container with Python 3.12.
   - Installs system dependencies like `build-essential` and `libpq-dev`.
   - Installs Python dependencies: `pytest`, `web.py`, `Cython`, `google-generativeai`, `google-genai`, and more.

3. **Repository Preparation**  
   - Clones the OpenLibrary repository into `/testbed`.
   - Checks out the commit specified in `task_id`.

4. **Infogami Setup (Optional)**  
   - Clones Infogami if not already present.
   - Installs in editable mode and verifies import.

5. **Run Gemini Agent**  
   - Executes `scripts/run_agent.py` to:
     - Run pre-validation tests.
     - Auto-reinstall broken modules if necessary.
     - Run post-validation tests.
     - Capture logs, prompts, and results.

6. **Post-Processing**  
   - Generates `changes.patch` to capture any modifications applied.
   - Produces an HTML report summarizing test results.
   - Uploads all artifacts for review in GitHub Actions.

---

## Artifacts Description

| File | Description |
|------|-------------|
| `pre_validation.log` | Logs from pre-validation tests before agent execution |
| `post_validation.log` | Logs from post-validation tests after agent execution |
| `prompts.md` | Record of prompts sent to Gemini AI agent |
| `results.json` | Structured evaluation results including errors, tests passing, and changes applied |
| `changes.patch` | Git diff of code changes applied by the agent |
| `swebench_result.html` | HTML report summarizing pre/post validation, warnings, errors, and resolution status |

---

## Test and Validation

- **Test Command:** `python -m pytest openlibrary/tests/core/test_imports.py -vv`
- **Validation Features:**
  - Detects `ModuleNotFoundError` and attempts auto-reinstall.
  - Retries test execution up to 3 times for broken modules.
  - Counts errors, passed tests, and warnings.
  - Records which modules were reinstalled.

---

## HTML Report

The HTML report provides:

- Pre-validation and post-validation results.
- Duration of the test run.
- Resolution status (whether errors were corrected).
- Summary of warnings and modules reinstalled.
- Snippet of changes if code was modified.

---

## Usage

1. Set `GEMINI_API_KEY` as a secret in your GitHub repository.
2. Trigger the workflow manually via GitHub Actions.
3. Provide a `task_id` corresponding to the OpenLibrary commit to evaluate.
4. Download and review artifacts after the run to inspect logs, results, and any code changes.

---

## Notes

- The workflow is containerized for consistent and reproducible environments.
- All Python dependencies are installed within the `/testbed` directory to avoid polluting global packages.
- HTML and JSON reports are generated for both human-friendly and programmatic consumption.
- `changes.patch` ensures full traceability of any automated modifications.

---

## Example JSON Output (`results.json`)

```json
{
  "pre_errors": 5,
  "post_errors": 0,
  "tests_passing": true,
  "change_applied": true
}
