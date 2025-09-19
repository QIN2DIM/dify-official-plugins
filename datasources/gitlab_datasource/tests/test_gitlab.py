# -*- coding: utf-8 -*-
import os
import base64
from types import SimpleNamespace

import certifi
import pytest
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from dify_plugin.entities.datasource import GetOnlineDocumentPageContentRequest
from datasources.gitlab import GitLabDataSource
import dotenv

dotenv.load_dotenv()
REAL_ACCESS_TOKEN = os.getenv("GITLAB_ACCESS_TOKEN")
REAL_BASE_URL = os.getenv("GITLAB_BASE_URL", "https://gitlab.com")
REQUIRES_REAL_CREDENTIALS = pytest.mark.skipif(
    not REAL_ACCESS_TOKEN,
    reason="GITLAB_ACCESS_TOKEN not set for GitLab integration tests",
)


def _build_real_datasource() -> GitLabDataSource:
    ds = GitLabDataSource(runtime=None, session=None)
    ds.runtime = SimpleNamespace(
        credentials={"access_token": REAL_ACCESS_TOKEN, "gitlab_url": REAL_BASE_URL}
    )
    return ds


def test_get_requests_session_configured(ds):
    session = ds._get_requests_session()
    assert session.verify == certifi.where()
    assert "https://" in session.adapters
    https_adapter = session.adapters["https://"]
    assert isinstance(https_adapter, HTTPAdapter)
    retries = https_adapter.max_retries
    assert isinstance(retries, Retry)
    assert retries.total == 3
    assert 429 in retries.status_forcelist
    assert "GET" in retries.allowed_methods


def test_get_projects_calls_make_request_with_expected_params(ds, monkeypatch):
    ds._get_gitlab_url()
    captured: dict = {}

    def fake_make_request(url, params=None):
        captured["url"] = url
        captured["params"] = params
        return [{"id": 101}]

    monkeypatch.setattr(ds, "_make_request", fake_make_request)

    result = ds._get_projects(max_projects=5)
    assert result == [{"id": 101}]
    assert captured["url"] == f"{ds.base_url}/projects"
    assert captured["params"] == {
        "per_page": 5,
        "order_by": "last_activity_at",
        "sort": "desc",
        "membership": True,
    }


def test_get_pages_collects_projects_and_related(ds, b64enc, monkeypatch):
    ds.gitlab_url = None
    ds.base_url = None
    ds.runtime.credentials["gitlab_url"] = "https://gitlab.example"
    user_info = {"id": 7, "name": "Tester", "avatar_url": "https://avatar"}
    project = {
        "id": 101,
        "name": "Demo",
        "path_with_namespace": "group/demo",
        "web_url": "https://gitlab.example/group/demo",
        "description": "Demo project",
        "default_branch": "main",
        "star_count": 5,
        "last_activity_at": "2024-01-01T00:00:00Z",
        "visibility": "public",
    }
    readme = {"encoding": "base64", "content": b64enc("# Demo"), "size": 8}
    issue = {
        "iid": 42,
        "title": "Fix bug",
        "updated_at": "2024-01-02T00:00:00Z",
        "web_url": "https://gitlab.example/group/demo/-/issues/42",
        "state": "opened",
        "author": {"username": "alice"},
        "created_at": "2024-01-01T00:00:00Z",
        "labels": ["bug"],
    }
    merge_request = {
        "iid": 6,
        "title": "Add feature",
        "updated_at": "2024-01-03T00:00:00Z",
        "web_url": "https://gitlab.example/group/demo/-/merge_requests/6",
        "state": "merged",
        "author": {"username": "bob"},
        "target_branch": "main",
        "source_branch": "feature",
        "created_at": "2024-01-02T00:00:00Z",
    }
    call_log = []

    def fake_make_request(url, params=None):
        call_log.append((url, params))
        if url.endswith("/user"):
            return user_info
        if url.endswith("/projects") and params == {
            "per_page": 20,
            "order_by": "last_activity_at",
            "sort": "desc",
            "membership": True,
        }:
            return [project]
        if url.endswith("/projects/101/repository/files/README.md"):
            return readme
        if url.endswith("/projects/101/issues"):
            assert params == {"state": "all", "per_page": 5, "order_by": "updated_at"}
            return [issue]
        if url.endswith("/projects/101/merge_requests"):
            assert params == {"state": "all", "per_page": 5, "order_by": "updated_at"}
            return [merge_request]
        raise AssertionError(f"Unexpected API call: {url} with {params}")

    monkeypatch.setattr(ds, "_make_request", fake_make_request)

    response = ds._get_pages({})
    assert response.result
    info = response.result[0]
    assert info.workspace_id == str(user_info["id"])
    assert info.workspace_name == f"{user_info['name']}'s GitLab"
    assert info.total == len(info.pages) == 4
    types = sorted(page.type for page in info.pages)
    assert types == ["file", "issue", "merge_request", "project"]
    project_page = next(page for page in info.pages if page.type == "project")
    assert project_page.page_id == f"project:{project['path_with_namespace']}"
    assert len(call_log) == 5


def test_get_pages_tolerates_missing_optional_resources(ds, monkeypatch):
    ds.gitlab_url = None
    ds.base_url = None
    ds.runtime.credentials["gitlab_url"] = "https://gitlab.example"
    user_info = {"id": 7, "name": "Tester", "avatar_url": ""}
    project = {
        "id": 201,
        "name": "Solo",
        "path_with_namespace": "group/solo",
        "web_url": "https://gitlab.example/group/solo",
        "description": "",
        "default_branch": "main",
        "star_count": 0,
        "last_activity_at": "2024-01-04T00:00:00Z",
        "visibility": "private",
    }
    call_log = []

    def fake_make_request(url, params=None):
        call_log.append((url, params))
        if url.endswith("/user"):
            return user_info
        if url.endswith("/projects"):
            return [project]
        raise ValueError("not found")

    monkeypatch.setattr(ds, "_make_request", fake_make_request)

    response = ds._get_pages({})
    info = response.result[0]
    assert info.total == 1
    assert len(info.pages) == 1
    assert info.pages[0].type == "project"
    assert call_log[0][0].endswith("/user")
    assert call_log[1][0].endswith("/projects")


def test_get_content_requires_token(ds):
    ds.runtime.credentials.pop("access_token", None)
    request = GetOnlineDocumentPageContentRequest(
        workspace_id="ws",
        page_id="project:group/demo",
        type="project",
    )
    with pytest.raises(ValueError, match="Access token not found"):
        list(ds._get_content(request))


@pytest.mark.parametrize(
    ("page_id", "expected_method"),
    [
        ("project:group/demo", "_get_project_content"),
        ("file:group/demo:README.md", "_get_file_content"),
        ("issue:group/demo:1", "_get_issue_content"),
        ("mr:group/demo:1", "_get_mr_content"),
    ],
)
def test_get_content_dispatches_by_prefix(ds, monkeypatch, page_id, expected_method):
    calls = []

    def fake_handler(target_page_id):
        calls.append(target_page_id)
        yield {"name": "page_id", "value": target_page_id}

    monkeypatch.setattr(ds, expected_method, fake_handler)

    request = GetOnlineDocumentPageContentRequest(
        workspace_id="ws",
        page_id=page_id,
        type="ignored",
    )
    result = list(ds._get_content(request))
    assert calls == [page_id]
    assert result[0]["value"] == page_id


def test_get_content_unsupported_page_type(ds):
    request = GetOnlineDocumentPageContentRequest(
        workspace_id="ws",
        page_id="unknown:page",
        type="unknown",
    )
    with pytest.raises(ValueError, match="Unsupported page type"):
        list(ds._get_content(request))


def test_get_project_content_includes_readme(ds, stub_create_message, b64enc, monkeypatch):
    stub_create_message(ds)
    ds._get_gitlab_url()
    project_path = "group/project"
    project_info = {
        "id": 301,
        "name": "Project",
        "path_with_namespace": project_path,
        "description": "Sample project",
        "default_branch": "main",
        "star_count": 2,
        "forks_count": 1,
        "created_at": "2024-01-01T00:00:00Z",
        "last_activity_at": "2024-01-02T00:00:00Z",
        "web_url": "https://gitlab.com/group/project",
        "topics": ["python"],
    }
    readme_info = {"encoding": "base64", "content": b64enc("Hello world"), "size": 11}

    def fake_make_request(url, params=None):
        if url.endswith("/projects/group%2Fproject"):
            return project_info
        if url.endswith("/projects/301/repository/files/README.md"):
            return readme_info
        raise AssertionError(f"Unexpected API call: {url}")

    monkeypatch.setattr(ds, "_make_request", fake_make_request)

    messages = list(ds._get_project_content(f"project:{project_path}"))
    content_value = next(m["value"] for m in messages if m["name"] == "content")
    assert "## README" in content_value
    assert "Hello world" in content_value
    assert any(m["name"] == "title" and m["value"] == project_info["name"] for m in messages)
    assert messages[-1]["value"] == "project"


def test_get_project_content_missing_readme(ds, stub_create_message, monkeypatch):
    stub_create_message(ds)
    ds._get_gitlab_url()
    project_path = "group/project"
    project_info = {
        "id": 302,
        "name": "Project",
        "path_with_namespace": project_path,
        "description": "",
        "default_branch": "main",
        "star_count": 0,
        "forks_count": 0,
        "created_at": "2024-01-01T00:00:00Z",
        "last_activity_at": "2024-01-01T00:00:00Z",
        "web_url": "https://gitlab.com/group/project",
        "topics": [],
    }

    def fake_make_request(url, params=None):
        if url.endswith("/projects/group%2Fproject"):
            return project_info
        if url.endswith("/projects/302/repository/files/README.md"):
            raise ValueError("README missing")
        raise AssertionError(f"Unexpected API call: {url}")

    monkeypatch.setattr(ds, "_make_request", fake_make_request)

    messages = list(ds._get_project_content(f"project:{project_path}"))
    content_value = next(m["value"] for m in messages if m["name"] == "content")
    assert "No README file found." in content_value


def test_get_project_content_readme_decode_error(ds, stub_create_message, monkeypatch):
    stub_create_message(ds)
    ds._get_gitlab_url()
    project_path = "group/project"
    project_info = {
        "id": 303,
        "name": "Project",
        "path_with_namespace": project_path,
        "description": "",
        "default_branch": "main",
        "star_count": 0,
        "forks_count": 0,
        "created_at": "2024-01-01T00:00:00Z",
        "last_activity_at": "2024-01-01T00:00:00Z",
        "web_url": "https://gitlab.com/group/project",
        "topics": [],
    }
    readme_info = {"encoding": "base64", "content": "/w=="}

    def fake_make_request(url, params=None):
        if url.endswith("/projects/group%2Fproject"):
            return project_info
        if url.endswith("/projects/303/repository/files/README.md"):
            return readme_info
        raise AssertionError(f"Unexpected API call: {url}")

    monkeypatch.setattr(ds, "_make_request", fake_make_request)

    messages = list(ds._get_project_content(f"project:{project_path}"))
    content_value = next(m["value"] for m in messages if m["name"] == "content")
    assert "Error decoding README content." in content_value


def test_get_file_content_decodes_markdown(ds, stub_create_message, b64enc, monkeypatch):
    stub_create_message(ds)
    ds._get_gitlab_url()
    project_path = "group/project"
    file_path = "docs/README.md"
    file_info = {"encoding": "base64", "content": b64enc("Contents")}

    def fake_make_request(url, params=None):
        if url.endswith("/projects/group%2Fproject/repository/files/docs%2FREADME.md"):
            return file_info
        raise AssertionError(f"Unexpected API call: {url}")

    monkeypatch.setattr(ds, "_make_request", fake_make_request)

    messages = list(ds._get_file_content(f"file:{project_path}:{file_path}"))
    content_value = next(m["value"] for m in messages if m["name"] == "content")
    assert content_value.startswith("# README.md")
    assert "Contents" in content_value
    assert any(m["name"] == "title" and m["value"] == "README.md" for m in messages)
    assert messages[-1]["value"] == "file"


def test_get_file_content_invalid_format(ds):
    with pytest.raises(ValueError, match="Invalid file page_id format"):
        list(ds._get_file_content("file:group/project"))


def test_get_file_content_decode_error(ds, monkeypatch):
    ds._get_gitlab_url()
    project_path = "group/project"
    file_path = "docs/README.md"
    file_info = {"encoding": "base64", "content": "/w=="}

    def fake_make_request(url, params=None):
        if url.endswith("/projects/group%2Fproject/repository/files/docs%2FREADME.md"):
            return file_info
        raise AssertionError(f"Unexpected API call: {url}")

    monkeypatch.setattr(ds, "_make_request", fake_make_request)

    with pytest.raises(ValueError, match="Failed to decode file content"):
        list(ds._get_file_content(f"file:{project_path}:{file_path}"))


def test_get_issue_content_includes_comments(ds, stub_create_message, monkeypatch):
    stub_create_message(ds)
    ds._get_gitlab_url()
    project_path = "group/project"
    issue_info = {
        "iid": 11,
        "title": "Bug",
        "web_url": "https://gitlab.com/group/project/-/issues/11",
        "state": "opened",
        "author": {"username": "alice"},
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "labels": ["bug"],
        "description": "Details",
    }
    comments = [
        {"system": True, "author": {"username": "bot"}, "created_at": "2024-01-02T00:00:00Z", "body": "ignored"},
        {"system": False, "author": {"username": "bob"}, "created_at": "2024-01-02T01:00:00Z", "body": "Looks good"},
    ]

    def fake_make_request(url, params=None):
        if url.endswith("/projects/group%2Fproject/issues/11"):
            return issue_info
        if url.endswith("/projects/group%2Fproject/issues/11/notes"):
            return comments
        raise AssertionError(f"Unexpected API call: {url}")

    monkeypatch.setattr(ds, "_make_request", fake_make_request)

    messages = list(ds._get_issue_content(f"issue:{project_path}:11"))
    content_value = next(m["value"] for m in messages if m["name"] == "content")
    assert "## Comments" in content_value
    assert "Looks good" in content_value
    assert "ignored" not in content_value
    assert messages[-1]["value"] == "issue"


def test_get_issue_content_invalid_format(ds):
    with pytest.raises(ValueError, match="Invalid issue page_id format"):
        list(ds._get_issue_content("issue:invalid"))


def test_get_issue_content_handles_comment_errors(ds, stub_create_message, monkeypatch):
    stub_create_message(ds)
    ds._get_gitlab_url()
    project_path = "group/project"
    issue_info = {
        "iid": 12,
        "title": "Bug",
        "web_url": "https://gitlab.com/group/project/-/issues/12",
        "state": "opened",
        "author": {"username": "alice"},
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "labels": [],
        "description": "",
    }

    def fake_make_request(url, params=None):
        if url.endswith("/projects/group%2Fproject/issues/12"):
            return issue_info
        if url.endswith("/projects/group%2Fproject/issues/12/notes"):
            raise ValueError("forbidden")
        raise AssertionError(f"Unexpected API call: {url}")

    monkeypatch.setattr(ds, "_make_request", fake_make_request)

    messages = list(ds._get_issue_content(f"issue:{project_path}:12"))
    content_value = next(m["value"] for m in messages if m["name"] == "content")
    assert "## Comments" not in content_value


def test_get_mr_content_includes_comments(ds, stub_create_message, monkeypatch):
    stub_create_message(ds)
    ds._get_gitlab_url()
    project_path = "group/project"
    mr_info = {
        "iid": 21,
        "title": "MR",
        "web_url": "https://gitlab.com/group/project/-/merge_requests/21",
        "state": "merged",
        "author": {"username": "alice"},
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "target_branch": "main",
        "source_branch": "feature",
        "description": "Summary",
    }
    comments = [
        {"system": False, "author": {"username": "bob"}, "created_at": "2024-01-02T01:00:00Z", "body": "Nice work"},
    ]

    def fake_make_request(url, params=None):
        if url.endswith("/projects/group%2Fproject/merge_requests/21"):
            return mr_info
        if url.endswith("/projects/group%2Fproject/merge_requests/21/notes"):
            return comments
        raise AssertionError(f"Unexpected API call: {url}")

    monkeypatch.setattr(ds, "_make_request", fake_make_request)

    messages = list(ds._get_mr_content(f"mr:{project_path}:21"))
    content_value = next(m["value"] for m in messages if m["name"] == "content")
    assert "## Comments" in content_value
    assert "Nice work" in content_value
    assert messages[-1]["value"] == "merge_request"


def test_get_mr_content_invalid_format(ds):
    with pytest.raises(ValueError, match="Invalid merge request page_id format"):
        list(ds._get_mr_content("mr:invalid"))


def test_get_mr_content_handles_comment_errors(ds, stub_create_message, monkeypatch):
    stub_create_message(ds)
    ds._get_gitlab_url()
    project_path = "group/project"
    mr_info = {
        "iid": 22,
        "title": "MR",
        "web_url": "https://gitlab.com/group/project/-/merge_requests/22",
        "state": "opened",
        "author": {"username": "alice"},
        "created_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-02T00:00:00Z",
        "target_branch": "main",
        "source_branch": "feature",
        "description": "",
    }

    def fake_make_request(url, params=None):
        if url.endswith("/projects/group%2Fproject/merge_requests/22"):
            return mr_info
        if url.endswith("/projects/group%2Fproject/merge_requests/22/notes"):
            raise ValueError("forbidden")
        raise AssertionError(f"Unexpected API call: {url}")

    monkeypatch.setattr(ds, "_make_request", fake_make_request)

    messages = list(ds._get_mr_content(f"mr:{project_path}:22"))
    content_value = next(m["value"] for m in messages if m["name"] == "content")
    assert "## Comments" not in content_value


@pytest.fixture(scope="module")
def real_workspace():
    if not REAL_ACCESS_TOKEN:
        pytest.skip("GITLAB_ACCESS_TOKEN not set for GitLab integration tests")
    ds = _build_real_datasource()
    response = ds._get_pages({})
    if not response.result:
        pytest.skip("GitLab API returned no accessible workspaces")
    return ds, response.result[0]


@pytest.mark.integration
@REQUIRES_REAL_CREDENTIALS
def test_integration_get_pages_and_project_content(real_workspace):
    ds, workspace = real_workspace
    project_page = next((page for page in workspace.pages if page.type == "project"), None)
    if project_page is None:
        pytest.skip("GitLab workspace returned no project pages")
    request = GetOnlineDocumentPageContentRequest(
        workspace_id=workspace.workspace_id,
        page_id=project_page.page_id,
        type=project_page.type,
    )
    messages = list(ds._get_content(request))
    assert messages
    variable_messages = [msg for msg in messages if msg.type == msg.MessageType.VARIABLE]
    names = {msg.message.variable_name for msg in variable_messages}
    assert {"content", "page_id", "title", "project", "type"} <= names


@pytest.mark.integration
@REQUIRES_REAL_CREDENTIALS
def test_integration_fetch_additional_resources_when_available(real_workspace):
    ds, workspace = real_workspace
    pages_by_type = {}
    for page in workspace.pages:
        pages_by_type.setdefault(page.type, page)
    found = []

    for page_type in ("file", "issue", "merge_request"):
        page = pages_by_type.get(page_type)
        if page is None:
            continue
        found.append(page_type)
        request = GetOnlineDocumentPageContentRequest(
            workspace_id=workspace.workspace_id,
            page_id=page.page_id,
            type=page.type,
        )
        messages = list(ds._get_content(request))
        assert messages
        assert any(
            msg.type == msg.MessageType.VARIABLE
            and msg.message.variable_name == "type"
            and msg.message.variable_value == page_type
            for msg in messages
        )

    if not found:
        pytest.skip("No additional GitLab resources available for integration assertions")
