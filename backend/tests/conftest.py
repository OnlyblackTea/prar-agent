import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def _isolate_host_gitconfig(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """隔离宿主 gitconfig：防止 commit.gpgsign 等宿主配置让测试内 git 卡在 pinentry。"""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "missing-global"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "missing-system"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
