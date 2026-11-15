"""Start the synthetic reviewer stack in one Linux container."""

from __future__ import annotations

import http.client
import os
import re
import secrets
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORLD = ROOT / "gigsim/fixtures/mini_world"
NEXT_SERVER = Path("/opt/gigbid-web/server.js")
CADDYFILE = Path("/etc/gigbid/Caddyfile")
HOST = "127.0.0.1"
GIGSIM_PORT = 8081
API_PORT = 8000
WEB_PORT = 3000
PERSONAS = ("ai_ml", "fullstack", "data_analyst")
BASE_ENV = ("HOME", "LANG", "LC_ALL", "PATH", "TMPDIR", "TZ")
BCRYPT_PATTERN = r"\$2[aby]\$(?:0[4-9]|[12][0-9]|3[01])\$[./a-zA-Z0-9]{53}"


@dataclass(frozen=True)
class DemoSettings:
    storage: Path
    port: int
    reviewer: str
    password_hash: str = field(repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> DemoSettings:
        password_hash = env.get("GIGBID_REVIEWER_HASH", "")
        if not re.fullmatch(BCRYPT_PATTERN, password_hash):
            raise ValueError("Set GIGBID_REVIEWER_HASH to a bcrypt password hash.")
        reviewer = env.get("GIGBID_REVIEWER_USER", "reviewer")
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", reviewer):
            raise ValueError("GIGBID_REVIEWER_USER contains unsupported characters.")
        raw_port = env.get("PORT", "10000")
        if not re.fullmatch(r"[0-9]{1,5}", raw_port) or not 1 <= int(raw_port) <= 65535:
            raise ValueError("PORT must be between 1 and 65535.")
        port = int(raw_port)
        if port in {GIGSIM_PORT, API_PORT, WEB_PORT}:
            raise ValueError("PORT conflicts with a private service.")
        storage = Path(env.get("GIGBID_STORAGE_DIR", "/var/lib/gigbid"))
        if not storage.is_absolute() or "?" in str(storage):
            raise ValueError("GIGBID_STORAGE_DIR must be an absolute filesystem path.")
        return cls(storage, port, reviewer, password_hash)


@contextmanager
def _storage_lock(storage: Path) -> Iterator[None]:
    import fcntl

    storage.mkdir(parents=True, exist_ok=True)
    with (storage / ".supervisor.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another demo instance uses this storage directory.") from error
        yield


def _sqlite_url(database: Path) -> str:
    return f"sqlite:///{database.as_posix()}"


def _probe(port: int, path: str, headers: dict[str, str] | None) -> bool:
    connection = http.client.HTTPConnection(HOST, port, timeout=1)
    try:
        connection.request("GET", path, headers=headers or {})
        with connection.getresponse() as response:
            return response.status == 200
    except (OSError, http.client.HTTPException):
        return False
    finally:
        connection.close()


class ChildExited(RuntimeError):
    def __init__(self, name: str, code: int) -> None:
        super().__init__(f"{name} exited with status {code}.")
        self.status = (128 - code if code < 0 else code) or 1


class StopRequested(Exception):
    def __init__(self, signum: int) -> None:
        self.status = 128 + signum


class Supervisor:
    def __init__(self) -> None:
        self.children: dict[str, subprocess.Popen] = {}
        self.stop_signal: int | None = None

    def request_stop(self, signum: int, _frame: object) -> None:
        self.stop_signal = signum

    def _check_stop(self) -> None:
        if self.stop_signal is not None:
            raise StopRequested(self.stop_signal)

    def check(self) -> None:
        self._check_stop()
        for name, process in self.children.items():
            code = process.poll()
            if code is not None:
                raise ChildExited(name, code)

    def start(self, name: str, command: list[str], env: dict[str, str], cwd: Path = ROOT) -> None:
        self.check()
        self.children[name] = subprocess.Popen(command, env=env, cwd=cwd)

    def run_seed(self, env: dict[str, str], database: Path) -> None:
        self.start(
            "GigSim seed",
            [
                sys.executable,
                "-m",
                "gigsim.seed",
                "--world",
                str(WORLD),
                "--db",
                _sqlite_url(database),
            ],
            env,
        )
        process = self.children["GigSim seed"]
        deadline = time.monotonic() + 60
        while process.poll() is None:
            self._check_stop()
            if time.monotonic() >= deadline:
                raise RuntimeError("Timed out seeding the synthetic simulator.")
            time.sleep(0.1)
        if process.returncode:
            raise ChildExited("GigSim seed", process.returncode)
        del self.children["GigSim seed"]
        self.check()

    def wait_ready(
        self, name: str, port: int, path: str, headers: dict[str, str] | None = None
    ) -> None:
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            self.check()
            if _probe(port, path, headers):
                self.check()
                return
            time.sleep(0.2)
        raise RuntimeError(f"Timed out waiting for {name} readiness.")

    def stop(self) -> None:
        children = tuple(reversed(self.children.values()))
        for process in children:
            if process.poll() is None:
                with suppress(ProcessLookupError):
                    process.terminate()
        deadline = time.monotonic() + 8
        for process in children:
            try:
                process.wait(timeout=max(0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    process.kill()
                process.wait()


def _seed_once(database: Path, supervisor: Supervisor, env: dict[str, str]) -> None:
    if database.exists():
        return
    with tempfile.TemporaryDirectory(dir=database.parent, prefix=".seed-") as work:
        staged = Path(work) / database.name
        supervisor.run_seed(env, staged)
        if not staged.is_file():
            raise RuntimeError("GigSim seed did not create a database.")
        with closing(sqlite3.connect(staged)) as connection:
            if connection.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
                raise RuntimeError("Unable to finalize the staged GigSim database.")
            if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
                raise RuntimeError("The staged GigSim database did not pass its integrity check.")
        supervisor.check()
        staged.replace(database)


def _serve(settings: DemoSettings, env: Mapping[str, str], supervisor: Supervisor) -> None:
    base = {name: env[name] for name in BASE_ENV if name in env}
    python_env = {
        **base,
        "PYTHONUNBUFFERED": "1",
        "MARKETPLACE_MODE": "gigsim",
        "MODEL_PROFILE": "test",
        "LANGSMITH_TRACING": "false",
        "GIGSIM_WORLD_PATH": str(WORLD),
        "GIGSIM_CLIENT_MODE": "rules",
        "GIGSIM_POLICY_VERSION": "v1",
        "GIGSIM_ERROR_RATE": "0",
        "GIGSIM_LATENCY_MS_MAX": "0",
    }
    database = settings.storage / "gigsim.sqlite3"
    checkpoints = settings.storage / "checkpoints.sqlite3"
    if not database.exists() and checkpoints.exists():
        raise RuntimeError("Restore the simulator database that belongs with these checkpoints.")
    _seed_once(database, supervisor, python_env)

    tokens = {persona: secrets.token_urlsafe(32) for persona in PERSONAS}
    python_env["GIGSIM_API_TOKENS"] = ",".join(
        f"{token}:persona_{persona}" for persona, token in tokens.items()
    )
    uvicorn = [sys.executable, "-m", "uvicorn"]
    supervisor.start(
        "GigSim",
        [*uvicorn, "gigsim.app:app", "--host", HOST, "--port", str(GIGSIM_PORT), "--workers", "1"],
        {
            **python_env,
            "GIGSIM_DB_URL": _sqlite_url(database),
            "GIGSIM_ADMIN_TOKEN": secrets.token_urlsafe(32),
        },
    )
    supervisor.wait_ready(
        "GigSim",
        GIGSIM_PORT,
        "/me",
        {"Authorization": f"Bearer {tokens['fullstack']}"},
    )
    supervisor.start(
        "API",
        [*uvicorn, "apps.api.main:app", "--host", HOST, "--port", str(API_PORT), "--workers", "1"],
        {
            **python_env,
            "GIGSIM_BASE_URL": f"http://{HOST}:{GIGSIM_PORT}",
            "GIGBID_WEB_CHECKPOINT": str(checkpoints),
        },
    )
    supervisor.wait_ready("API", API_PORT, "/api/v1/personas")
    supervisor.start(
        "Web",
        ["node", str(NEXT_SERVER)],
        {
            **base,
            "HOSTNAME": HOST,
            "PORT": str(WEB_PORT),
            "NEXT_TELEMETRY_DISABLED": "1",
            "NODE_ENV": "production",
        },
        cwd=NEXT_SERVER.parent,
    )
    supervisor.wait_ready("Web", WEB_PORT, "/")
    supervisor.start(
        "Caddy",
        ["caddy", "run", "--config", str(CADDYFILE), "--adapter", "caddyfile"],
        {
            **base,
            "PORT": str(settings.port),
            "GIGBID_REVIEWER_USER": settings.reviewer,
            "GIGBID_REVIEWER_HASH": settings.password_hash,
        },
    )
    while True:
        supervisor.check()
        time.sleep(0.2)


def main() -> int:
    supervisor = Supervisor()
    os.umask(0o077)
    signal.signal(signal.SIGTERM, supervisor.request_stop)
    signal.signal(signal.SIGINT, supervisor.request_stop)
    try:
        settings = DemoSettings.from_env(os.environ)
        with _storage_lock(settings.storage):
            try:
                _serve(settings, os.environ, supervisor)
            finally:
                supervisor.stop()
    except StopRequested as requested:
        return requested.status
    except ChildExited as error:
        print(str(error), file=sys.stderr)
        return error.status
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
