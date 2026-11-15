import subprocess

import pytest

from conftest import test_database as database_container


def test_database_uses_isolated_project_and_cleans_up(monkeypatch):
    calls = []
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "production")
    monkeypatch.setenv("YOURFEED_TEST_DATABASE_URL", "postgresql://production/db")

    def run(args, **kwargs):
        calls.append((args, kwargs))
        output = "127.0.0.1:49152\n" if "port" in args else ""
        return subprocess.CompletedProcess(args, 0, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    with database_container() as url:
        assert url == "postgresql://yourfeed_test:yourfeed_test@127.0.0.1:49152/yourfeed_test"
    assert calls[0][0][calls[0][0].index("-p") + 1].startswith("yourfeed-test-")
    assert len({args[args.index("-p") + 1] for args, _ in calls}) == 1
    assert all("COMPOSE_PROJECT_NAME" not in kwargs["env"] for _, kwargs in calls)
    assert "down" in calls[-1][0]
    assert "--volumes" in calls[-1][0]


def test_start_failure_cleans_up(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if "up" in args:
            raise subprocess.CalledProcessError(1, args, stderr="startup failed")
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="startup failed"):
        with database_container():
            pytest.fail("startup should fail")
    assert "down" in calls[-1]


def test_start_and_cleanup_failures_are_both_reported(monkeypatch):
    def run(args, **kwargs):
        message = "startup failed" if "up" in args else "cleanup failed"
        raise subprocess.CalledProcessError(1, args, stderr=message)

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="startup failed") as error:
        with database_container():
            pytest.fail("startup should fail")
    assert any("cleanup failed" in note for note in error.value.__notes__)


def test_cleanup_failure_is_reported(monkeypatch):
    def run(args, **kwargs):
        if "down" in args:
            raise subprocess.CalledProcessError(1, args, stderr="cleanup failed")
        return subprocess.CompletedProcess(args, 0, stdout="127.0.0.1:49152" if "port" in args else "", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="cleanup failed"):
        with database_container():
            pass


def test_cleanup_runs_when_test_fails(monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, stdout="127.0.0.1:49152" if "port" in args else "", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ValueError, match="test failed"):
        with database_container():
            raise ValueError("test failed")
    assert "down" in calls[-1]
