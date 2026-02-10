#!/bin/bash
set -euxo pipefail

echo "--- Starting Repository Setup ---"
echo "Task ID: ${TASK_ID:-<missing>}"

if [ -z "${TASK_ID:-}" ]; then
  echo "ERROR: TASK_ID is not set"
  exit 1
fi

REPO_DIR="/testbed"
REPO_URL="https://github.com/internetarchive/openlibrary.git"

# --------------------------------------------------
# 1. Clone if needed
# --------------------------------------------------
if [ ! -d "$REPO_DIR/.git" ]; then
    echo "Cloning OpenLibrary into $REPO_DIR"
    git clone "$REPO_URL" "$REPO_DIR"
else
    echo "Repository already exists"
fi

cd "$REPO_DIR"

# --------------------------------------------------
# 2. Extract commit hash SAFELY
# --------------------------------------------------
COMMIT_HASH="${TASK_ID##*-}"

if ! git cat-file -e "$COMMIT_HASH^{commit}" 2>/dev/null; then
    echo "ERROR: Commit hash $COMMIT_HASH does not exist"
    exit 1
fi

echo "Using commit hash: $COMMIT_HASH"

# --------------------------------------------------
# 3. Force clean state (CRITICAL)
# --------------------------------------------------
echo "Resetting repository to a clean state"
git fetch origin
git reset --hard "$COMMIT_HASH"
git clean -xfd

# --------------------------------------------------
# 4. Confirm state
# --------------------------------------------------
echo "Current HEAD:"
git rev-parse HEAD

echo "--- Repository Setup Complete ---"
