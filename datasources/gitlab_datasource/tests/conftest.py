"""Common pytest fixtures for GitLab datasource tests."""
import os
import base64
import pytest
import dotenv

dotenv.load_dotenv()


@pytest.fixture
def credentials_env() -> dict:
    """Return credentials from environment or safe defaults for unit tests."""
    access_token = os.environ.get("GITLAB_ACCESS_TOKEN", "test-token")
    gitlab_url = os.environ.get("GITLAB_BASE_URL", "https://gitlab.com")
    return {"access_token": access_token, "gitlab_url": gitlab_url}


@pytest.fixture
def ds(credentials_env):
    """Create a GitLabDataSource with a minimal runtime carrying credentials."""
    from types import SimpleNamespace
    from datasources.gitlab import GitLabDataSource

    instance = GitLabDataSource(runtime=None, session=None)
    instance.runtime = SimpleNamespace(credentials=credentials_env)
    return instance


@pytest.fixture
def stub_create_message(monkeypatch):
    """Patch create_variable_message to return plain dict for easy assertions."""

    def _apply(target):
        monkeypatch.setattr(
            target,
            "create_variable_message",
            lambda name, value: {"name": name, "value": value},
        )
        return target

    return _apply


@pytest.fixture
def b64enc():
    """Helper to base64-encode text as GitLab API returns."""

    def _enc(text: str) -> str:
        return base64.b64encode(text.encode("utf-8")).decode("utf-8")

    return _enc


class FakeResponse:
    """Minimal Response stub for unit tests."""

    def __init__(self, json_data, status_code=200, text="", headers=None):
        self._json_data = json_data
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def json(self):
        if isinstance(self._json_data, Exception):
            raise self._json_data
        return self._json_data


class RecordingSession:
    """Session stub that records the last call for assertions."""

    def __init__(self, route_handler):
        self._route_handler = route_handler
        self.last_url = None
        self.last_headers = None
        self.last_params = None
        self.verify = True

    def get(self, url, headers=None, params=None, timeout=30):
        self.last_url = url
        self.last_headers = headers or {}
        self.last_params = params or {}
        result = self._route_handler(url, params or {})
        if isinstance(result, FakeResponse):
            return result
        # Auto-wrap plain payloads into FakeResponse
        return FakeResponse(result, status_code=200)


@pytest.fixture
def recording_session_factory():
    """Factory to provide a RecordingSession around a custom route handler."""

    def _factory(handler):
        return RecordingSession(handler)

    return _factory
