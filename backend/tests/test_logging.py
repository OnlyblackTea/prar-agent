"""日志基建测试：structlog 配置、request_id 传播、Middleware。"""

import json
import logging
from io import StringIO

import pytest
import structlog
from fastapi.testclient import TestClient

from app.core.logging import (
    get_logger,
    request_id_var,
    setup_logging,
)


@pytest.fixture(autouse=True)
def _reset_logging() -> None:
    """每次测试后清理 stdlib logging 状态，避免测试间污染。

    structlog 不提供 reset_config；本模块测试均显式调用 setup_logging，
    会自行覆盖前序配置，故仅需清理 stdlib handlers。
    """
    yield
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)
    # structlog 无 reset_config；不清理，依赖各测试显式 setup_logging


# ===== T1-T2: setup_logging 不抛异常且 root logger 有 handler =====


def test_setup_logging_console_mode() -> None:
    setup_logging(log_level="INFO", json_format=False)
    root = logging.getLogger()
    assert len(root.handlers) == 1


def test_setup_logging_json_mode() -> None:
    setup_logging(log_level="INFO", json_format=True)
    root = logging.getLogger()
    assert len(root.handlers) == 1


# ===== T3: request_id ContextVar 传播到日志输出 =====


def test_request_id_var_propagation() -> None:
    setup_logging(log_level="INFO", json_format=True)

    # 捕获格式化后的日志输出
    string_io = StringIO()
    capture_handler = logging.StreamHandler(string_io)
    # 复用当前 root logger 的 formatter（ProcessorFormatter）
    existing_formatter = logging.getLogger().handlers[0].formatter
    capture_handler.setFormatter(existing_formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(capture_handler)
    root.setLevel(logging.INFO)

    token = request_id_var.set("abc")
    try:
        get_logger("test").info("hello")
    finally:
        request_id_var.reset(token)

    output = string_io.getvalue().strip()
    assert output, "应有日志输出"
    data = json.loads(output)
    assert data["request_id"] == "abc"


# ===== T4-T5: RequestContextMiddleware =====


def test_request_context_middleware_sets_request_id(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert "X-Request-ID" in resp.headers
    assert len(resp.headers["X-Request-ID"]) == 12


def test_request_context_middleware_respects_incoming_header(
    client: TestClient,
) -> None:
    resp = client.get("/health", headers={"X-Request-ID": "custom-123"})
    assert resp.status_code == 200
    assert resp.headers["X-Request-ID"] == "custom-123"


# ===== T6: get_logger 返回 BoundLogger =====


def test_get_logger_returns_bound_logger() -> None:
    setup_logging(log_level="INFO", json_format=False)
    logger = get_logger("test")
    from structlog._config import BoundLoggerLazyProxy

    assert isinstance(logger, structlog.stdlib.BoundLogger | BoundLoggerLazyProxy)
