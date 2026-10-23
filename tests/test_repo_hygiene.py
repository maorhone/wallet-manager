"""NFR10: the repository is public, so tracked files hold placeholders only."""

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
SK_TOKEN = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")
JWT_SHAPE = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")
URL_HOST = re.compile(r"\b[a-z][a-z0-9+.-]*://(?:[^/\s@\"'<>]*@)?([A-Za-z0-9.-]+)")
EMAIL_HOST = re.compile(r"[\w.+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
DN_PART = re.compile(r"\bDC=([A-Za-z0-9_-]+)")

ALLOWED_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "github.com", "raw.githubusercontent.com",
    "registry.npmjs.org", "pypi.org", "files.pythonhosted.org", "playwright.dev",
    "json-schema.org", "www.w3.org", "mantine.dev", "react.dev", "vite.dev", "vitejs.dev",
    "docs.litellm.ai", "docs.pydantic.dev", "fastapi.tiangolo.com",
    "docs.aws.amazon.com", "learn.microsoft.com", "help.openai.com", "docs.cloud.google.com",
    "framework.finops.org",
}  # fmt: skip
ALLOWED_SUFFIXES = (".example", ".test", ".invalid")
ALLOWED_DC = {"corp", "example"}
LOCK_FILES = {"uv.lock", "package-lock.json"}


def _tracked() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True, text=True
    ).stdout
    return [ROOT / p for p in out.split("\0") if p]


def _host_ok(host: str) -> bool:
    host = host.lower().rstrip(".")
    return (
        "." not in host  # single-label names (fake, postgres) are test aliases, not hosts
        or host in ALLOWED_HOSTS
        or host.endswith(ALLOWED_SUFFIXES)
        or host in {s.lstrip(".") for s in ALLOWED_SUFFIXES}
    )


def _denylist() -> list[str]:
    path = ROOT / ".public-denylist"
    if not path.exists():
        return []
    lines = (ln.strip() for ln in path.read_text().splitlines())
    return [ln.lower() for ln in lines if ln and not ln.startswith("#")]


def test_tracked_files_have_no_secrets_or_internal_names():
    deny = _denylist()
    problems: list[str] = []
    for path in _tracked():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # binary asset
        rel = path.relative_to(ROOT)
        lower = text.lower()
        for rx, what in ((PRIVATE_KEY, "private key"), (SK_TOKEN, "sk- token"), (JWT_SHAPE, "JWT")):
            if rx.search(text):
                problems.append(f"{rel}: {what}")
        if path.name not in LOCK_FILES:
            for host in URL_HOST.findall(text):
                if not _host_ok(host):
                    problems.append(f"{rel}: host {host}")
        for host in EMAIL_HOST.findall(text):
            if not _host_ok(host) and not host.endswith((".py", ".js", ".ts", ".tsx")):
                problems.append(f"{rel}: email host {host}")
        for part in DN_PART.findall(text):
            if part.lower() not in ALLOWED_DC:
                problems.append(f"{rel}: DN component DC={part}")
        problems += [f"{rel}: denylisted '{w}'" for w in deny if w in lower]
    assert not problems, "\n".join(sorted(set(problems)))
