#!/bin/sh
# Run an OpenAI-compatible client (default: codex) with a project tag header.
project="${WM_PROJECT:-$(basename "$PWD")}"
header="${WM_PROJECT_TAG_HEADER:-x-litellm-tags}"
export OPENAI_DEFAULT_HEADERS="{\"$header\":\"project:$project,tool:codex\"}"
exec "${WM_CLIENT:-codex}" "$@"
