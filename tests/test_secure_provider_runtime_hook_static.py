from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_session_routes_call_secure_provider_gate_before_model_probe():
    body = (ROOT / "routes" / "session_routes.py").read_text(encoding="utf-8")

    assert "security_mode: str = Form(\"\")" in body
    assert "enforce_session_provider_runtime_gate" in body

    first_guard = body.index("enforce_session_provider_runtime_gate")
    first_probe = body.index("list_model_ids")
    assert first_guard < first_probe


def test_session_routes_map_secure_provider_gate_errors_to_400():
    body = (ROOT / "routes" / "session_routes.py").read_text(encoding="utf-8")

    assert "except (SecureProviderRuntimeError, ValueError) as exc:" in body
    assert "raise HTTPException(400, str(exc))" in body
