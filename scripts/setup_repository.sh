#!/bin/bash
set -e

# These variables are usually passed from your workflow env
# REPO_NAME="internetarchive/openlibrary"
# BASE_COMMIT="<the_commit_hash_from_swe_bench>"

echo "Setting up repository: $REPO_NAME at commit $BASE_COMMIT"

# 1. Clone the target repository into a 'testbed' directory
git clone https://github.com/$REPO_NAME /testbed
cd /testbed

# 2. Reset to the specific version the agent needs to fix
git reset --hard $BASE_COMMIT

# 3. Install the specific dependencies for OpenLibrary
# OpenLibrary often uses pip or a specific requirements file
if [ -f "requirements.txt" ]; then
    pip install -r requirements.txt
fi

echo "Repository setup complete at /testbed"
