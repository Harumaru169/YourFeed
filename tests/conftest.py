import os
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

# The test client sends this Host header; the app does not accept it by default.
os.environ.setdefault("YOURFEED_ALLOWED_HOSTS", "testserver")

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def test_database():
    """Start an isolated Compose project and always try to remove it."""
    project = f"yourfeed-test-{uuid4().hex}"
    env = {key: value for key, value in os.environ.items() if not key.startswith("COMPOSE_")}
    compose = ["docker", "compose", "--env-file", os.devnull, "-f", str(ROOT / "docker-compose.test.yaml"),
               "-p", project]

    def run(*args, timeout):
        try:
            result = subprocess.run([*compose, *args], env=env, capture_output=True, text=True,
                                    timeout=timeout, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            details = getattr(exc, "stderr", None) or getattr(exc, "stdout", None) or str(exc)
            raise RuntimeError(f"Docker Compose {' '.join(args)} failed for {project}: {details}") from exc
        return result.stdout.strip()

    try:
        run("up", "--wait", "--wait-timeout", "60", timeout=300)
        address = run("port", "db", "5432", timeout=30)
        host, port = address.rsplit(":", 1)
        if host != "127.0.0.1" or not port.isdecimal() or not 1 <= int(port) <= 65535:
            raise RuntimeError(f"Unexpected test database address: {address!r}")
        yield f"postgresql://yourfeed_test:yourfeed_test@127.0.0.1:{port}/yourfeed_test"
    finally:
        original_error = sys.exception()
        try:
            run("down", "--volumes", "--timeout", "10", timeout=60)
        except RuntimeError as cleanup_error:
            if original_error is None:
                raise
            original_error.add_note(f"Cleanup failed: {cleanup_error}")


@pytest.fixture(scope="session")
def test_database_url():
    with test_database() as url:
        yield url


@pytest.fixture
def engine(monkeypatch, test_database_url):
    """A migrated, empty database in the isolated test container."""
    monkeypatch.setenv("DATABASE_URL", test_database_url)
    from yourfeed.config import database_url
    engine = create_engine(database_url())
    try:
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
        command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
        yield engine
    finally:
        engine.dispose()
