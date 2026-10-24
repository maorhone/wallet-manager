#!/bin/sh
# Run Claude Code with a project tag header. Usage: claude-with-project.sh [claude args]
project="${WM_PROJECT:-$(basename "$PWD")}"
header="${WM_PROJECT_TAG_HEADER:-x-litellm-tags}"
export ANTHROPIC_CUSTOM_HEADERS="$header: project:$project,tool:claude-code"
exec claude "$@"
