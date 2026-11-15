import signal
import sqlite3
from contextlib import closing
from unittest.mock import Mock

import pytest

from scripts.hosted_demo import (
    ChildExited,
    DemoSettings,
    StopRequested,
    Supervisor,
    _seed_once,
    _serve,
)

BCRYPT_HASH = "$2b$12$" + "." * 53


@pytest.mark.parametrize(
    ("overrides", "message"),
    (
        ({"GIGBID_REVIEWER_HASH": ""}, "bcrypt"),
        ({"GIGBID_REVIEWER_HASH": "plaintext"}, "bcrypt"),
        ({"GIGBID_REVIEWER_HASH": "$2b$40$" + "." * 53}, "bcrypt"),
        ({"GIGBID_REVIEWER_USER": "bad username"}, "GIGBID_REVIEWER_USER"),
        ({"PORT": "0"}, "PORT"),
        ({"PORT": "8081"}, "private service"),
        ({"GIGBID_STORAGE_DIR": "relative/path"}, "absolute"),
    ),
)
def test_invalid_hosted_configuration_fails_before_startup(tmp_path, overrides, message):
    env = {
        "GIGBID_STORAGE_DIR": str(tmp_path),
        "GIGBID_REVIEWER_HASH": BCRYPT_HASH,
        **overrides,
    }
    with pytest.raises(ValueError, match=message):
        DemoSettings.from_env(env)


def test_only_public_proxy_starts_after_private_services_are_ready(tmp_path):
    env = {
        "GIGBID_STORAGE_DIR": str(tmp_path),
        "GIGBID_REVIEWER_HASH": BCRYPT_HASH,
        "GIGSIM_API_TOKENS": "must-be-replaced",
        "GIGSIM_ADMIN_TOKEN": "must-be-replaced",
        "GROQ_API_KEY": "not-passed-to-children",
        "MARKETPLACE_MODE": "upwork",
    }
    settings = DemoSettings.from_env(env)
    assert settings.port == 10000
    assert settings.reviewer == "reviewer"
    assert BCRYPT_HASH not in repr(settings)
    (tmp_path / "gigsim.sqlite3").touch()

    supervisor = Mock(spec=Supervisor)
    supervisor.check.side_effect = StopRequested(signal.SIGTERM)
    with pytest.raises(StopRequested):
        _serve(settings, env, supervisor)

    assert [(call[0], call.args[0] if call.args else None) for call in supervisor.mock_calls] == [
        ("start", "GigSim"),
        ("wait_ready", "GigSim"),
        ("start", "API"),
        ("wait_ready", "API"),
        ("start", "Web"),
        ("wait_ready", "Web"),
        ("start", "Caddy"),
        ("check", None),
    ]
    services = {call.args[0]: call.args[2] for call in supervisor.start.call_args_list}
    tokens = services["GigSim"]["GIGSIM_API_TOKENS"]
    assert tokens == services["API"]["GIGSIM_API_TOKENS"]
    assert tokens != env["GIGSIM_API_TOKENS"]
    assert services["GigSim"]["GIGSIM_ADMIN_TOKEN"] != env["GIGSIM_ADMIN_TOKEN"]
    assert services["GigSim"]["GIGSIM_ERROR_RATE"] == "0"
    assert services["GigSim"]["GIGSIM_CLIENT_MODE"] == "rules"
    assert services["API"]["MARKETPLACE_MODE"] == "gigsim"
    assert services["API"]["GIGSIM_BASE_URL"] == "http://127.0.0.1:8081"
    assert services["API"]["GIGBID_WEB_CHECKPOINT"] == str(tmp_path / "checkpoints.sqlite3")
    assert services["Web"]["HOSTNAME"] == "127.0.0.1"
    assert services["Caddy"]["GIGBID_REVIEWER_HASH"] == BCRYPT_HASH
    assert all("GROQ_API_KEY" not in child for child in services.values())
    assert all("GIGSIM_ADMIN_TOKEN" not in services[name] for name in ("API", "Web", "Caddy"))
    assert all("GIGSIM_API_TOKENS" not in services[name] for name in ("Web", "Caddy"))
    assert all("GIGBID_REVIEWER_HASH" not in services[name] for name in ("GigSim", "API", "Web"))
    supervisor.run_seed.assert_not_called()


def test_seed_is_atomic_and_not_repeated(tmp_path):
    database = tmp_path / "gigsim.sqlite3"
    supervisor = Mock(spec=Supervisor)

    def seed(_env, staged):
        assert not database.exists()
        assert staged.parent.parent == tmp_path
        with closing(sqlite3.connect(staged)) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE demo (value text)")
            connection.execute("INSERT INTO demo VALUES ('seeded')")
            connection.commit()

    supervisor.run_seed.side_effect = seed
    _seed_once(database, supervisor, {})
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT value FROM demo").fetchone() == ("seeded",)
        connection.execute("UPDATE demo SET value = 'preserved'")
        connection.commit()

    _seed_once(database, supervisor, {})
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT value FROM demo").fetchone() == ("preserved",)
    assert supervisor.run_seed.call_count == 1


def test_replaced_marketplace_cannot_reuse_existing_approvals(tmp_path):
    (tmp_path / "checkpoints.sqlite3").touch()
    settings = DemoSettings(tmp_path, 10000, "reviewer", BCRYPT_HASH)
    supervisor = Mock(spec=Supervisor)

    with pytest.raises(RuntimeError, match="Restore the simulator database"):
        _serve(settings, {}, supervisor)

    assert not supervisor.mock_calls


def test_failed_seed_does_not_publish_partial_database(tmp_path):
    database = tmp_path / "gigsim.sqlite3"
    supervisor = Mock(spec=Supervisor)

    def seed(_env, staged):
        staged.write_text("interrupted", encoding="utf-8")
        raise RuntimeError("seed failed")

    supervisor.run_seed.side_effect = seed
    with pytest.raises(RuntimeError, match="seed failed"):
        _seed_once(database, supervisor, {})
    assert list(tmp_path.iterdir()) == []


def test_supervisor_propagates_child_failure_and_handles_stop_signal():
    supervisor = Supervisor()
    process = Mock()
    process.poll.return_value = 17
    supervisor.children["Web"] = process
    with pytest.raises(ChildExited) as failed:
        supervisor.check()
    assert failed.value.status == 17

    supervisor.request_stop(signal.SIGTERM, None)
    with pytest.raises(StopRequested) as stopped:
        supervisor.check()
    assert stopped.value.status == 143
