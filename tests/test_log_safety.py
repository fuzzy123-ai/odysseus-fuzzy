from core.log_safety import redact_url


def test_strips_userinfo():
    assert redact_url("https://user:pass@host.example/v1/models") == "https://host.example/v1/models"


def test_strips_query_and_fragment():
    assert redact_url("https://host.example/v1?api_key=secret#frag") == "https://host.example/v1"


def test_keeps_port_and_path():
    assert redact_url("http://host.example:8080/api/tags") == "http://host.example:8080/api/tags"


def test_ipv6_host_keeps_brackets():
    assert redact_url("https://user:pass@[2001:db8::1]:8443/v1") == "https://[2001:db8::1]:8443/v1"


def test_empty_and_none():
    assert redact_url("") == ""
    assert redact_url(None) == ""
