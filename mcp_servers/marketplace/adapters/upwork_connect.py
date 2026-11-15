"""Connect a GigBid user to their real Upwork account, on this computer.

    uv run python -m mcp_servers.marketplace.adapters.upwork_connect connect
    uv run python -m mcp_servers.marketplace.adapters.upwork_connect status
    uv run python -m mcp_servers.marketplace.adapters.upwork_connect disconnect

`connect` opens Upwork's own consent page once, saves the sign-in encrypted per user, and
picks the user's freelancer account. It also saves Upwork's full tool reference
(get_tool_help) and a few small read-only samples, which the live adapter's field mapping
is checked against. It never creates, submits or sends anything.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet
from dotenv import load_dotenv
from mcp import ClientSession

from mcp_servers.marketplace.adapters.upwork_auth import (
    DEFAULT_CALLBACK_PORT,
    PLACEHOLDER_KEYS,
    EncryptedTokenStorage,
    SignInRequired,
    TokenKeyError,
    load_fernet,
    upwork_session,
)
from mcp_servers.marketplace.adapters.upwork_discovery import DiscoveryError
from mcp_servers.runtime import ROOT

STORAGE_DIR = ROOT / ".cache" / "upwork"
REFERENCE_FILE = "reference.json"
HELP_TOOLS = (
    "find_jobs",
    "get_profile",
    "get_freelancer_dashboard",
    "list_freelancer_proposals",
    "manage_proposals",
    "get_preview",
    "get_messages",
    "send_message",
)
KEY_LINE = re.compile(r"^TOKEN_ENCRYPTION_KEY=.*$", re.M)


class ConnectError(RuntimeError):
    """An operator-facing error without tokens or provider payloads."""


def ensure_key(env_file: Path) -> tuple[Fernet, bool]:
    """Return the token key, creating one in .env if none is set. Reports whether it was new."""
    load_dotenv(env_file, override=False)
    if os.environ.get("TOKEN_ENCRYPTION_KEY", "").strip() not in PLACEHOLDER_KEYS:
        return load_fernet(), False
    key = Fernet.generate_key().decode()
    line = f"TOKEN_ENCRYPTION_KEY={key}"
    text = env_file.read_text(encoding="utf-8") if env_file.exists() else ""
    if KEY_LINE.search(text):
        text = KEY_LINE.sub(line, text, count=1)
    else:
        text = f"{text}{'' if not text or text.endswith(chr(10)) else chr(10)}{line}\n"
    env_file.write_text(text, encoding="utf-8")
    os.environ["TOKEN_ENCRYPTION_KEY"] = key
    return load_fernet(key), True


def _text(result: Any) -> str:
    return "\n".join(getattr(part, "text", "") for part in result.content)


def _json(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return None


def _dicts(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for inner in value.values():
            yield from _dicts(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _dicts(inner)


def pick_freelancer_account(accounts: Any) -> dict[str, Any]:
    """Choose the freelancer account from list_accounts; agencies and clients come after."""
    candidates = [d for d in _dicts(accounts) if isinstance(d.get("org_uid"), str)]
    if not candidates:
        raise ConnectError("Upwork returned no accounts with an org_uid.")

    def rank(account: dict[str, Any]) -> int:
        words = " ".join(str(v) for v in account.values() if isinstance(v, str)).lower()
        return 0 if "freelancer" in words else 1 if "agency" in words else 2

    chosen = min(candidates, key=rank)
    keep = {"org_uid", "type", "account_type", "name", "title", "role"}
    return {k: v for k, v in chosen.items() if k in keep and isinstance(v, str)}


async def _call(session: ClientSession, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await session.call_tool(f"upwork__{tool}", arguments)
    return {"args": arguments, "is_error": bool(result.isError), "text": _text(result)}


async def capture_reference(session: ClientSession, org_uid: str) -> dict[str, Any]:
    """Upwork's own tool reference plus a few small read-only samples."""
    reference: dict[str, Any] = {
        "captured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "help": {},
        "samples": {},
    }
    for tool in HELP_TOOLS:
        reference["help"][tool] = await _call(session, "get_tool_help", {"tool_name": tool})
    reads = {
        "recommended_jobs": ("find_jobs", "smart_search", {"limit": 3}),
        "connects_balance": ("get_profile", "connects_balance", {}),
        "proposals": ("list_freelancer_proposals", "list", {}),
        "rooms": ("get_messages", "list_rooms", {}),
    }
    for label, (tool, action, params) in reads.items():
        args = {"action": action, "org_uid": org_uid, "params": params}
        reference["samples"][label] = await _call(session, tool, args)
    jobs = _json(reference["samples"]["recommended_jobs"]["text"])
    job_id = next(
        (d.get("id") or d.get("job_id") for d in _dicts(jobs) if d.get("id") or d.get("job_id")),
        None,
    )
    if job_id:
        args = {"action": "get", "org_uid": org_uid, "params": {"id": str(job_id)}}
        reference["samples"]["job_details"] = await _call(session, "find_jobs", args)
    return reference


async def connect(
    user: str,
    *,
    storage_dir: Path = STORAGE_DIR,
    env_file: Path = ROOT / ".env",
    port: int = DEFAULT_CALLBACK_PORT,
    timeout: float = 180,
    launch_browser: Callable[[str], bool] | None = None,
    httpx_client_factory: Any = None,
) -> dict[str, Any]:
    fernet, new_key = ensure_key(env_file)
    storage = EncryptedTokenStorage(storage_dir, user, fernet)
    options: dict[str, Any] = {"httpx_client_factory": httpx_client_factory}
    if launch_browser is not None:
        options["launch_browser"] = launch_browser
    async with (
        asyncio.timeout(timeout + 120),
        upwork_session(storage, interactive=True, port=port, timeout=timeout, **options) as session,
    ):
        accounts = await _call(session, "list_accounts", {})
        if accounts["is_error"]:
            raise ConnectError("Upwork could not list your accounts.")
        account = pick_freelancer_account(_json(accounts["text"]))
        storage.set_account(account)
        reference = await capture_reference(session, account["org_uid"])
    reference_path = storage_dir / REFERENCE_FILE
    reference_path.write_text(json.dumps(reference, ensure_ascii=False, indent=2), "utf-8")
    return {"account": account, "new_key": new_key, "reference": reference_path}


def status(user: str, *, storage_dir: Path = STORAGE_DIR, env_file: Path = ROOT / ".env") -> str:
    load_dotenv(env_file, override=False)
    storage = EncryptedTokenStorage(storage_dir, user, load_fernet())
    if not storage.connected():
        return f"User '{user}' is not connected to Upwork."
    account = storage.get_account() or {}
    label = account.get("name") or account.get("type") or "account"
    expires = storage.expires_at()
    when = (
        datetime.fromtimestamp(expires, UTC).isoformat(timespec="minutes") if expires else "unknown"
    )
    return (
        f"User '{user}' is connected to Upwork ({label}, org {account.get('org_uid', '?')}). "
        f"Access token valid until {when}; it refreshes automatically."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Connect GigBid to your Upwork account.")
    parser.add_argument("command", choices=("connect", "status", "disconnect"))
    parser.add_argument("--user", default=os.environ.get("GIGBID_USER", "local"))
    parser.add_argument("--port", type=int, default=DEFAULT_CALLBACK_PORT)
    parser.add_argument("--timeout", type=int, default=180, help="Seconds allowed for sign-in.")
    args = parser.parse_args()
    for name in ("mcp", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
    try:
        if args.command == "status":
            print(status(args.user))
            return
        if args.command == "disconnect":
            load_dotenv(ROOT / ".env", override=False)
            storage = EncryptedTokenStorage(STORAGE_DIR, args.user, load_fernet())
            print("Disconnected." if storage.disconnect() else "Nothing to disconnect.")
            return
        result = asyncio.run(connect(args.user, port=args.port, timeout=args.timeout))
    except (TokenKeyError, ConnectError, DiscoveryError, SignInRequired) as error:
        parser.exit(1, f"{error}\n")
    except TimeoutError:
        parser.exit(1, "Upwork sign-in timed out.\n")
    except OSError as error:
        if "address" in str(error).lower():
            parser.exit(1, f"Port {args.port} is busy. Close the other program or use --port.\n")
        parser.exit(1, "Connecting to Upwork failed.\n")
    except Exception:
        # SDK errors can include provider payloads. Keep secrets out of CLI output.
        parser.exit(1, "Upwork sign-in or the account lookup did not complete.\n")
    if result["new_key"]:
        print("Created TOKEN_ENCRYPTION_KEY in .env. Keep .env private; without the key the")
        print("saved sign-in cannot be read and you would need to connect again.")
    account = result["account"]
    label = account.get("name") or account.get("type") or "account"
    print(f"Connected user '{args.user}' to Upwork ({label}, org {account['org_uid']}).")
    print(f"Saved Upwork's tool reference to {result['reference'].relative_to(ROOT)}.")


if __name__ == "__main__":
    main()
