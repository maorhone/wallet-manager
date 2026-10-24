# Project tag hooks

Wallet Manager groups spend by project when each request carries a tag header.
LiteLLM reads the comma-separated `x-litellm-tags` request header into
`LiteLLM_SpendLogs.request_tags`. The header name is configurable on the
Wallet Manager side with `WM_PROJECT_TAG_HEADER` (default `x-litellm-tags`).

Tag format: `project:<name>` and, optionally, `tool:<name>`.
Requests without a `project:` tag are reported as "ללא תיוג" (untagged).

## Claude Code

Claude Code forwards extra headers from `ANTHROPIC_CUSTOM_HEADERS`
(one `Name: value` per line). `claude-with-project.sh` sets it from the
current directory name (or `WM_PROJECT`).

## Codex / OpenAI-compatible clients

Pass the header through the client's extra-headers option, for example
`OPENAI_DEFAULT_HEADERS='{"x-litellm-tags":"project:my-project,tool:codex"}'`
for SDKs that support it, or `codex-with-project.sh`.

## Generic

```sh
curl -H "x-litellm-tags: project:my-project,tool:curl" "$LITELLM_URL/v1/chat/completions" ...
```
