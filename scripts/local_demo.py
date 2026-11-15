"""Start a persistent, loopback-only GigBid demo with synthetic data."""

import argparse
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

from apps.api.settings import load_simulator_account_tokens

ROOT = Path(__file__).resolve().parents[1]
WORLD = ROOT / "gigsim/fixtures/mini_world"
WEB = ROOT / "apps/web"


def sqlite_url(path: Path) -> str:
    return f"sqlite:///{path.resolve().as_posix()}"


def seed_if_missing(database: Path, world: Path) -> None:
    if database.exists():
        return
    database.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=database.parent, prefix=".seed-") as folder:
        staged = Path(folder) / database.name
        subprocess.run(
            [
                sys.executable,
                "-m",
                "gigsim.seed",
                "--world",
                str(world),
                "--db",
                sqlite_url(staged),
            ],
            cwd=ROOT,
            check=True,
        )
        with closing(sqlite3.connect(staged)) as connection:
            if connection.execute("PRAGMA journal_mode=DELETE").fetchone() != ("delete",):
                raise RuntimeError("Could not finalize the seeded GigSim database.")
        try:
            database.hardlink_to(staged)
        except FileExistsError:
            pass


def wait_ready(url: str, processes: list[subprocess.Popen], seconds: int = 60) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if any(process.poll() is not None for process in processes):
            raise RuntimeError("A local demo service exited during startup.")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for {url}")


def serve(args: argparse.Namespace, database: Path) -> None:
    ports = (args.web_port, args.api_port, args.simulator_port)
    if len(set(ports)) != len(ports) or any(not 1 <= port <= 65535 for port in ports):
        raise SystemExit("Choose three distinct ports between 1 and 65535.")
    for port in ports:
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise SystemExit(f"Local port {port} is in use. Choose another port.") from None

    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm is None:
        raise SystemExit("Install Node.js with npm before starting the browser demo.")
    if (
        not (WEB / "node_modules/.bin/next").exists()
        and not (WEB / "node_modules/.bin/next.cmd").exists()
    ):
        subprocess.run([npm, "ci"], cwd=WEB, check=True)

    simulator_url = f"http://127.0.0.1:{args.simulator_port}"
    api_url = f"http://127.0.0.1:{args.api_port}"
    web_url = f"http://127.0.0.1:{args.web_port}"
    accounts = load_simulator_account_tokens()
    if args.auth:
        accounts = {account: secrets.token_urlsafe(32) for account in accounts}
    env = {
        **os.environ,
        "MARKETPLACE_MODE": "gigsim",
        "MODEL_PROFILE": "test",
        "GIGSIM_CLIENT_MODE": "rules",
        "GIGSIM_ERROR_RATE": "0",
        "GIGSIM_LATENCY_MS_MAX": "0",
        "GIGSIM_DB_URL": sqlite_url(database),
        "GIGSIM_BASE_URL": simulator_url,
        "GIGSIM_API_TOKENS": ",".join(f"{token}:{account}" for account, token in accounts.items()),
        "GIGBID_API_URL": api_url,
        "GIGBID_WEB_CHECKPOINT": str(database.with_name("approvals.sqlite")),
        "GIGBID_CHECKPOINT_DSN": "",
        "GIGBID_QUEUE_URL": "",
        "GIGBID_AUTH_MODE": "local" if args.auth else "demo",
        "GIGBID_AUTH_DB": str(database.with_name("accounts.sqlite")),
        "GIGBID_AUTH_WEB_ORIGIN": web_url,
        "GIGBID_NEXT_AUTH": "1" if args.auth else "",
        "NEXT_TELEMETRY_DISABLED": "1",
    }
    if args.auth:
        env["GIGSIM_ADMIN_TOKEN"] = secrets.token_urlsafe(32)
    processes: list[subprocess.Popen] = []
    try:
        for module, port, url in (
            ("gigsim.app:app", args.simulator_port, simulator_url),
            ("apps.api.main:app", args.api_port, api_url),
        ):
            command = [
                sys.executable,
                "-m",
                "uvicorn",
                module,
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ]
            processes.append(subprocess.Popen(command, cwd=ROOT, env=env))
            wait_ready(f"{url}/health", processes)

        node = shutil.which("node")
        if node is None:
            raise SystemExit("Install Node.js before starting the browser demo.")
        command = [
            node,
            str(WEB / "node_modules/next/dist/bin/next"),
            "dev",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(args.web_port),
        ]
        processes.append(subprocess.Popen(command, cwd=WEB, env=env))
        wait_ready(f"{web_url}/health/deps", processes)
        print(f"GigBid is ready at {web_url}. Press Ctrl+C to stop.", flush=True)
        while all(process.poll() is None for process in processes):
            time.sleep(0.5)
        raise RuntimeError("A local demo service stopped.")
    except KeyboardInterrupt:
        pass
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--world", type=Path, default=WORLD)
    parser.add_argument("--web-port", type=int)
    parser.add_argument("--api-port", type=int)
    parser.add_argument("--simulator-port", type=int)
    parser.add_argument(
        "--auth", action="store_true", help="Run with separate, sign-in-only workspaces."
    )
    parser.add_argument("--seed-only", action="store_true")
    args = parser.parse_args()
    folder = "local-auth" if args.auth else "local-demo"
    database = (args.database or ROOT / ".cache" / folder / "gigsim.sqlite").resolve()
    args.web_port = args.web_port if args.web_port is not None else (3011 if args.auth else 3010)
    args.api_port = args.api_port if args.api_port is not None else (18390 if args.auth else 18380)
    args.simulator_port = (
        args.simulator_port if args.simulator_port is not None else (18391 if args.auth else 18381)
    )
    seed_if_missing(database, args.world.resolve())
    if not args.seed_only:
        serve(args, database)


if __name__ == "__main__":
    main()
