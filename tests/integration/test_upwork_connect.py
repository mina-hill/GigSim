"""The connect command signs in once, picks the freelancer account and saves the reference."""

import json
import socket

from cryptography.fernet import Fernet

from mcp_servers.marketplace.adapters.upwork_connect import (
    HELP_TOOLS,
    connect,
    ensure_key,
    pick_freelancer_account,
    status,
)
from tests.support.upwork_mock import MockUpwork

ACCOUNTS = {
    "accounts": [
        {"org_uid": "client-1", "type": "Client", "name": "Mina (client)"},
        {"org_uid": "free-1", "type": "Freelancer", "name": "Mina Hill", "token": "x"},
    ]
}


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def mock_upwork() -> MockUpwork:
    jobs = {"jobs": [{"id": "~021", "title": "RAG chatbot"}]}
    return MockUpwork(
        tools={
            "upwork__list_accounts": lambda _: ACCOUNTS,
            "upwork__get_tool_help": lambda a: f"Reference for {a['tool_name']}",
            "upwork__find_jobs": lambda a: (
                jobs if a["action"] == "smart_search" else jobs["jobs"][0]
            ),
            "upwork__get_profile": lambda _: {"connects_balance": 42},
            "upwork__list_freelancer_proposals": lambda _: {"proposals": []},
            "upwork__get_messages": lambda _: {"rooms": []},
        }
    )


async def test_connect_saves_sign_in_account_and_reference_without_writes(tmp_path, monkeypatch):
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("APP_ENV=development\nTOKEN_ENCRYPTION_KEY=replace_me\n")
    mock = mock_upwork()
    result = await connect(
        "mina",
        storage_dir=tmp_path / "upwork",
        env_file=env_file,
        port=free_port(),
        timeout=10,
        launch_browser=mock.browser,
        httpx_client_factory=mock.client_factory(),
    )
    assert result["new_key"] and "replace_me" not in env_file.read_text()
    assert result["account"] == {"org_uid": "free-1", "type": "Freelancer", "name": "Mina Hill"}

    reference = json.loads(result["reference"].read_text())
    assert set(reference["help"]) == set(HELP_TOOLS)
    assert reference["samples"]["connects_balance"]["text"] == '{"connects_balance": 42}'
    assert reference["samples"]["job_details"]["args"]["params"] == {"id": "~021"}
    assert "access-1" not in result["reference"].read_text()

    called = {name.removeprefix("upwork__") for name, _ in mock.calls}
    assert called <= {
        "list_accounts",
        "get_tool_help",
        "find_jobs",
        "get_profile",
        "list_freelancer_proposals",
        "get_messages",
    }
    assert "connected to Upwork (Mina Hill, org free-1)" in status(
        "mina", storage_dir=tmp_path / "upwork", env_file=env_file
    )


def test_an_existing_key_is_kept(tmp_path, monkeypatch):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("TOKEN_ENCRYPTION_KEY", key)
    env_file = tmp_path / ".env"
    _, created = ensure_key(env_file)
    assert not created and not env_file.exists()


def test_a_missing_env_file_gets_a_key(tmp_path, monkeypatch):
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    env_file = tmp_path / ".env"
    _, created = ensure_key(env_file)
    assert created and env_file.read_text().startswith("TOKEN_ENCRYPTION_KEY=")


def test_the_freelancer_account_is_preferred_over_client_and_agency():
    accounts = [
        {"org_uid": "a", "type": "Agency"},
        {"org_uid": "c", "type": "Client"},
        {"org_uid": "f", "type": "Freelancer"},
    ]
    assert pick_freelancer_account({"data": accounts})["org_uid"] == "f"
