#!/usr/bin/env bash
set -euo pipefail

python3 promptbus.py submit --user alice --title "payments tests" --prompt "Add coverage around the payments endpoint"
python3 promptbus.py submit --user bob --title "API tests" --prompt "Please add integration tests for /v1/payments"
python3 promptbus.py submit --user chris --title "docs" --prompt "Update README with usage examples"

python3 promptbus.py dedupe
python3 promptbus.py list --kind all

# Dry run to show command expansion
python3 promptbus.py run --dry-run --executor "echo running task {task_id} with {prompt_file}"
