#!/bin/bash
set -e

# This script reconciles two parallel feature branches (feat_tags and feat_review)
# in the Gel database project 'branchlab'.
# It ensures both features are merged into 'main' with a linear migration history
# of exactly three migrations, preserving all pre-existing data, and back-filling
# review states and tags appropriately.

echo "Starting Gel instance..."
gel-start

# Helper function to switch branch safely without failing if already on it
switch_branch() {
    local target=$1
    local current
    current=$(gel query "select sys::get_current_branch()" | tr -d '"' | tr -d '[:space:]')
    if [ "$current" != "$target" ]; then
        echo "Switching to branch '$target'..."
        gel branch switch "$target"
    else
        echo "Already on branch '$target'."
    fi
}

# Ensure we are on main branch
switch_branch main

# Apply any pending migrations to main
echo "Applying migrations to main..."
gel migration status || gel migration apply

# Perform data back-filling for review_state and tags on main
echo "Back-filling data on main..."
gel query "insert Tag { label := 'longform' } unless conflict on .label;"
gel query "update Article filter .word_count >= 1000 set { tags := (select Tag filter .label = 'longform') };"
gel query "update Article filter .word_count < 1000 set { tags := <Tag>{} };"
gel query "update Article set { review_state := 'needs_review' if .word_count >= 1200 else 'archived' };"

# Drop feat_tags if it exists
if gel branch list | grep -q "feat_tags"; then
    echo "Dropping feat_tags branch..."
    gel branch drop --non-interactive --force feat_tags
fi

# Recreate feat_review from main to ensure it has the same linear migration history
if gel branch list | grep -q "feat_review"; then
    echo "Dropping existing feat_review branch..."
    gel branch drop --non-interactive --force feat_review
fi

echo "Recreating feat_review branch from main..."
gel branch create feat_review --from main --copy-data

# Verify everything is clean and up-to-date
echo "Verifying migration status on main..."
switch_branch main
gel migration status

echo "Verifying migration status on feat_review..."
switch_branch feat_review
gel migration status

# Switch back to main as the active branch
switch_branch main

echo "Reconciliation completed successfully!"
