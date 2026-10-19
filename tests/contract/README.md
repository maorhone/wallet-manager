# Contract tests (A2 / A3 / A5)

Each test runs against `target=fake` (in-process fake LiteLLM, always) and `target=real`
(skipped unless configured). They create their own objects through the typed client and call
the model with the plaintext key from `/key/generate`; the key lives in memory only.

## Running against a real LiteLLM

Set all of:

- `LITELLM_BASE_URL` – proxy URL
- `LITELLM_MASTER_KEY`
- `LITELLM_DATABASE_URL` – SQLAlchemy asyncpg URL (read-only role is enough)
- `LITELLM_TEST_MODEL` – model name of a **mock model priced so each request costs exactly 1 credit**

Extra requirements:

- A3 needs **2 proxy replicas** behind `LITELLM_BASE_URL` (§15.2) and a model with ~500 ms latency
  (the fake's latency is configured with `/_test/config`; the real model must provide it itself).
- Use a clean database/prefix: tests create `team-c`, `u1`, `u2` and do not clean up.
