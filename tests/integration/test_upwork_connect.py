"""The connect command signs in once, picks the freelancer account and saves the reference."""

import json
import socket

from cryptography.fernet import Fernet

from mcp_servers.marketplace.adapters.upwork_connect import (
    HELP_TOOLS,
    check,
    confirm,
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


async def connected(tmp_path, monkeypatch, tools):
    monkeypatch.delenv("TOKEN_ENCRYPTION_KEY", raising=False)
    env_file = tmp_path / ".env"
    mock = mock_upwork()
    mock.tools.update(tools)
    port = free_port()
    await connect(
        "mina",
        storage_dir=tmp_path / "upwork",
        env_file=env_file,
        port=port,
        timeout=10,
        launch_browser=mock.browser,
        httpx_client_factory=mock.client_factory(),
    )
    options = {
        "port": port,
        "timeout": 10,
        "launch_browser": mock.browser,
        "httpx_client_factory": mock.client_factory(),
    }
    return mock, {
        "storage_dir": tmp_path / "upwork",
        "env_file": env_file,
        "session_options": options,
    }


PREVIEW = {
    "status": "ok",
    "preview_id": "pv_1",
    "job_title": "RAG chatbot",
    "charged_amount": 40,
    "connects_cost": 14,
    "connects_balance": 120,
    "cover_letter": "I built a cited RAG bot.",
}


async def test_confirm_submits_only_after_the_person_types_submit(tmp_path, monkeypatch):
    mock, where = await connected(
        tmp_path,
        monkeypatch,
        {
            "upwork__get_preview": lambda _: PREVIEW,
            "upwork__confirm_preview": lambda _: {"status": "ok"},
        },
    )
    shown = []
    assert not await confirm("mina", "pv_1", ask=lambda _: "yes", show=shown.append, **where)
    assert not any(name == "upwork__confirm_preview" for name, _ in mock.calls)
    assert "Connects: 14 of your 120" in shown and "I built a cited RAG bot." in shown

    assert await confirm("mina", "pv_1", ask=lambda _: "SUBMIT", show=shown.append, **where)
    confirms = [args for name, args in mock.calls if name == "upwork__confirm_preview"]
    assert confirms == [
        {
            "action": "confirm",
            "org_uid": "free-1",
            "params": {"type": "proposal", "preview_id": "pv_1"},
        }
    ]


async def test_check_reads_through_the_live_adapter(tmp_path, monkeypatch):
    row = {
        "id": "77",
        "title": "Python data pipeline",
        "job_type": "fixed",
        "budget": "$800.00",
        "skills": ["Python", "ETL"],
        "client": {},
    }

    def find_jobs(args):
        if args["action"] == "get":
            return {"status": "ok", "connects_cost": 9, "connects_balance": 42}
        return {"status": "ok", "jobs": [row]}

    mock, where = await connected(
        tmp_path,
        monkeypatch,
        {
            "upwork__find_jobs": find_jobs,
            "upwork__get_profile": lambda _: {"status": "ok", "balance": {"connectsBalance": 42}},
        },
    )
    lines = await check("mina", **where)
    assert lines[0] == "Connects balance: 42"
    assert lines[1].startswith("Job 77: Python data pipeline ($800.0 fixed")
    assert lines[-1] == "Applying to job 77 would cost 9 Connects."
    assert not {"upwork__manage_proposals", "upwork__confirm_preview"} & {n for n, _ in mock.calls}
