"""Static regressions for Docker/devops hardening contracts."""

import ast
import re
from pathlib import Path

import yaml
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILES = [
    ROOT / "docker-compose.yml",
    ROOT / "docker-compose.gpu-nvidia.yml",
    ROOT / "docker-compose.gpu-amd.yml",
]
TEST_DOCS = [
    ROOT / "tests" / "README.md",
    ROOT / "tests" / "TESTING_STANDARD.md",
    ROOT / "tests" / "LAYOUT_INVENTORY.md",
]


def _compose_env_names(path: Path) -> set[str]:
    compose = yaml.safe_load(path.read_text(encoding="utf-8"))
    env = compose["services"]["odysseus"]["environment"]
    return {entry.split("=", 1)[0] for entry in env}


def _upload_limit_env_names() -> set[str]:
    source = (ROOT / "src" / "upload_limits.py").read_text(encoding="utf-8")
    return set(re.findall(r'"(ODYSSEUS_[A-Z_]*BYTES)"', source)) | {
        "ODYSSEUS_CHAT_UPLOAD_MAX_BYTES"
    }


def _cors_allow_methods() -> list[str]:
    tree = ast.parse((ROOT / "app.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            if "CORS_ALLOW_METHODS" in names:
                return ast.literal_eval(node.value)
    raise AssertionError("CORS_ALLOW_METHODS not found")


def test_compose_files_forward_every_upload_limit_env_var():
    expected = _upload_limit_env_names()
    assert expected
    for path in COMPOSE_FILES:
        assert expected <= _compose_env_names(path), path.name


def test_base_compose_provisions_internal_ollama_service():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    odysseus = services["odysseus"]
    assert odysseus["build"]["args"]["INSTALL_OPTIONAL"] == "${INSTALL_OPTIONAL:-false}"
    assert "ollama" in odysseus["depends_on"]
    assert "LLM_HOST=${LLM_HOST:-ollama}" in odysseus["environment"]
    assert "LLM_HOSTS=${LLM_HOSTS:-ollama}" in odysseus["environment"]
    assert "OLLAMA_BASE_URL=${OLLAMA_BASE_URL:-http://ollama:11434}" in odysseus["environment"]
    assert "/var/run/docker.sock:/var/run/docker.sock" not in odysseus["volumes"]
    assert "group_add" not in odysseus

    ollama = services["ollama"]
    assert ollama["image"] == "docker.io/ollama/ollama:latest"
    assert ollama["restart"] == "unless-stopped"
    assert "ports" not in ollama
    assert "ollama-data:/root/.ollama" in ollama["volumes"]
    assert "ollama-data" in compose["volumes"]


def test_homeserver_env_defaults_point_at_compose_ollama():
    for relpath in (
        "ops/homeserver/setup-odysseus-env.sh",
        "ops/homeserver/fix-odysseus-env-defaults.sh",
    ):
        text = (ROOT / relpath).read_text(encoding="utf-8")
        assert "LLM_HOST=ollama" in text or "add_if_missing LLM_HOST ollama" in text
        assert "LLM_HOSTS=ollama" in text or "add_if_missing LLM_HOSTS ollama" in text
        assert (
            "OLLAMA_BASE_URL=http://ollama:11434" in text
            or "add_if_missing OLLAMA_BASE_URL http://ollama:11434" in text
        )
        assert "INSTALL_OPTIONAL" in text


def test_docker_image_preloads_runtime_optional_dependencies():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    entrypoint = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")

    assert "AS realesrgan-wheels" in dockerfile
    assert "docker-${DOCKER_CLI_VERSION}.tgz" in dockerfile
    assert "libmagic1" in dockerfile
    assert "libxcb1" in dockerfile
    assert "python-magic" in requirements
    assert "NPM_CONFIG_CACHE=/app/.cache/npm" in dockerfile
    assert "npx -y @playwright/mcp@latest --version" in dockerfile
    assert "/app/.cache/npm" in entrypoint
    assert "DOCKER_SOCK" in entrypoint
    assert 'exec "$GOSU_BIN" "$ODY_USER" "$@"' in entrypoint


def test_docker_entrypoint_does_not_resolve_root_commands_from_app_local_path():
    script = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    path_export = script.index('export PATH="/app/.local/bin:$PATH"')
    gosu_capture = script.index('GOSU_BIN="$(command -v gosu)"')
    python_capture = script.index('PYTHON_BIN="$(command -v python)"')
    setup_call = script.index('"$GOSU_BIN" "$ODY_USER" "$PYTHON_BIN" /app/setup.py')
    final_exec = script.index('exec "$GOSU_BIN" "$ODY_USER" "$@"')

    assert gosu_capture < path_export < setup_call
    assert python_capture < path_export < setup_call
    assert final_exec > path_export


def test_docker_entrypoint_ownership_repair_stays_inside_expected_mounts():
    script = (ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
    assert "find /app -xdev" in script
    for path in ("/app/data", "/app/logs", "/app/.ssh", "/app/.cache", "/app/.local"):
        assert f"-path {path}" in script
    assert "mount_root_for" in script
    assert "is_broad_mount_root" in script
    assert "Skipping recursive ownership repair" in script


def test_dockerignore_excludes_secrets_editor_backups():
    patterns = set((ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines())
    assert {
        "secrets.env",
        "secrets.env.*",
        "secrets.env~",
        ".secrets.env.swp",
        ".secrets.env.swo",
        "**/#secrets.env#",
    } <= patterns
    assert "!secrets.env.example" in patterns


def test_cors_allow_methods_include_patch():
    methods = _cors_allow_methods()
    assert "PATCH" in methods


def test_patch_preflight_is_allowed_by_configured_cors_methods():
    async def patched(_request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/api/document/1", patched, methods=["PATCH"])])
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://client.local"],
        allow_credentials=True,
        allow_methods=_cors_allow_methods(),
        allow_headers=["Content-Type"],
    )

    response = TestClient(app).options(
        "/api/document/1",
        headers={
            "Origin": "http://client.local",
            "Access-Control-Request-Method": "PATCH",
        },
    )

    assert response.status_code == 200


def test_testing_docs_use_project_venv_for_python_validation():
    stale_patterns = [
        "python3 -m pytest",
        "python3 -m py_compile",
        "Focused `pytest`",
        "`pytest` on neighboring",
        ".venv/bin/python",
    ]
    for path in TEST_DOCS:
        text = path.read_text(encoding="utf-8")
        for stale in stale_patterns:
            assert stale not in text, f"{path.name} still contains {stale!r}"
