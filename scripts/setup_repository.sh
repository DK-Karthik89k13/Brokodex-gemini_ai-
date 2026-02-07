#!/bin/bash
set -e

echo "--- Starting Repository Setup ---"
echo "Task ID: $TASK_ID"

# 1. Clone the OpenLibrary repository into /testbed
# We use /testbed because your workflow logic points there
if [ ! -d "/testbed/.git" ]; then
    echo "Cloning OpenLibrary..."
    git clone https://github.com/internetarchive/openlibrary.git /testbed
else
    echo "Testbed already exists, skipping clone."
fi

cd /testbed

# 2. Checkout the specific broken state
# For the specific task ID in your default input:
# internetarchive__openlibrary-c4eebe6677acc4629cb541a98d5e91311444f5d4
# The commit hash is the last part of the ID: c4eebe6677acc4629cb541a98d5e91311444f5d4
COMMIT_HASH=$(echo $TASK_ID | rev | cut -d'-' -f1 | rev)

echo "Checking out commit: $COMMIT_HASH"
git checkout $COMMIT_HASH

# 3. Optional: Install dependencies if not in the container image
# pip install -e .

echo "--- Setup Complete ---"
