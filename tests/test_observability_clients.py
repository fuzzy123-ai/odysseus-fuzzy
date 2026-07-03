import pytest

from src.observability_clients import (
    ObservabilityClientConfig,
    ObservabilityClientError,
    query_loki_readonly,
    query_prometheus_readonly,
    readiness,
)


def test_readiness_blocks_without_configured_endpoints():
    status = readiness()

    assert status["status"] == "blocked"
    assert status["reason"] == "observability_endpoints_not_configured"
    assert status["read_only"] is True
    assert status["writes_performed"] is False


def test_prometheus_query_uses_fake_transport_and_redacts_labels():
    calls = []

    def fake_transport(method, url, params, timeout):
        calls.append((method, url, params, timeout))
        return {
            "status": "success",
            "data": {
                "resultType": "vector",
                "result": [
                    {
                        "metric": {
                            "surface": "telegram",
                            "component": "poller",
                            "chat_id": "must-not-leak",
                            "filename": "private.pdf",
                        },
                        "value": [123, "2"],
                    }
                ],
            },
        }

    result = query_prometheus_readonly(
        'telegram_poll_failure_total{surface="telegram"}',
        config=ObservabilityClientConfig(
            prometheus_url="http://127.0.0.1:9090",
            enabled=True,
            transport=fake_transport,
        ),
    )

    assert calls[0][0] == "GET"
    assert calls[0][1].endswith("/api/v1/query")
    assert result["status"] == "success"
    assert result["result"]["result_count"] == 1
    assert result["result"]["results"][0]["labels"] == {"component": "poller", "surface": "telegram"}
    assert result["result"]["results"][0]["last_value"] == 2.0
    assert "query" not in result
    assert result["query_ref"].startswith("sha256:")


def test_loki_query_summarizes_streams_without_log_lines():
    def fake_transport(_method, _url, _params, _timeout):
        return {
            "status": "success",
            "data": {
                "resultType": "streams",
                "result": [
                    {
                        "stream": {"surface": "scheduler", "severity": "error", "private_label": "hidden"},
                        "values": [["100", "private raw log line"], ["200", "another line"]],
                    }
                ],
            },
        }

    result = query_loki_readonly(
        '{surface="scheduler"}',
        config=ObservabilityClientConfig(
            loki_url="http://127.0.0.1:3100",
            enabled=True,
            transport=fake_transport,
        ),
    )

    stream = result["result"]["streams"][0]
    assert result["status"] == "success"
    assert stream["labels"] == {"severity": "error", "surface": "scheduler"}
    assert stream["line_count"] == 2
    assert stream["first_ts"] == "100"
    assert stream["last_ts"] == "200"
    assert stream["log_lines_included"] is False
    assert "private raw log line" not in str(result)


def test_queries_are_config_gated_and_secret_safe():
    blocked = query_prometheus_readonly("up", limit=500)

    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "prometheus_not_configured"
    assert blocked["limit"] == 100
    assert blocked["records"] == ()

    with pytest.raises(ObservabilityClientError):
        query_loki_readonly('secret{authorization="Bearer token"}')

    with pytest.raises(ObservabilityClientError):
        query_prometheus_readonly(r'up{path="C:\Users\nkatz\private"}')
