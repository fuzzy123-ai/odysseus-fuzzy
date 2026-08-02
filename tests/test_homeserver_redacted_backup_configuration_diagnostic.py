from __future__ import annotations

import json
import stat
from types import SimpleNamespace

import pytest

from ops.homeserver import redacted_backup_configuration_diagnostic as module


def _stat(kind, mode, uid, *, nlink=1, size=32):
    return SimpleNamespace(
        st_mode=kind | mode,
        st_uid=uid,
        st_nlink=nlink,
        st_size=size,
    )


def _dependencies():
    uid = 1000
    values = {
        module.RESTIC_BINARY: _stat(stat.S_IFREG, 0o755, 0),
        module.SOURCE: _stat(stat.S_IFDIR, 0o700, uid),
        module.REPOSITORY: _stat(stat.S_IFDIR, 0o700, uid),
        module.CONFIG_PATH: _stat(stat.S_IFREG, 0o600, uid),
        module.PASSWORD_FILE: _stat(stat.S_IFREG, 0o600, uid),
        module.CONFIG_DIRECTORY: _stat(stat.S_IFDIR, 0o700, uid),
    }
    return {
        "process_environment": {},
        "owner_lookup": lambda _owner: SimpleNamespace(pw_uid=uid),
        "lstat": lambda path: values[path],
        "mount_checker": lambda _path: True,
        "config_reader": lambda: (
            "RESTIC_PASSWORD_FILE=" + module.PASSWORD_FILE + "\n"
        ),
        "path_exists": lambda _path: False,
        "platform_checker": lambda: True,
    }, values


def test_exact_ready_contract_is_boolean_only_and_value_free():
    dependencies, _values = _dependencies()
    result = module.collect_backup_configuration_diagnostic(**dependencies)

    assert result["status"] == "observed"
    assert result["error_code"] == "none"
    assert result["contract_ready"] is True
    assert all(result[key] is True for key in module._PROOFS)
    assert all(result[key] is False for key in module._VISIBILITY)
    assert result["retry_permitted"] is False
    assert module.validate_envelope(result)
    encoded = json.dumps(result, sort_keys=True)
    assert module.PASSWORD_FILE not in encoded
    assert module.CONFIG_PATH not in encoded


@pytest.mark.parametrize(
    ("proof", "mutate"),
    [
        (
            "process_environment_safe",
            lambda dependencies, _values: dependencies.update(
                process_environment={"RESTIC_PASSWORD": "synthetic"}
            ),
        ),
        (
            "process_environment_safe",
            lambda dependencies, _values: dependencies.update(
                process_environment={"RESTIC_PASSWORD": ""}
            ),
        ),
        (
            "process_environment_safe",
            lambda dependencies, _values: dependencies.update(
                process_environment={"RESTIC_PASSWORD_COMMAND": ""}
            ),
        ),
        (
            "owner_resolved",
            lambda dependencies, _values: dependencies.update(
                owner_lookup=lambda _owner: (_ for _ in ()).throw(KeyError())
            ),
        ),
        (
            "backup_mount_present",
            lambda dependencies, _values: dependencies.update(
                mount_checker=lambda _path: False
            ),
        ),
        (
            "restic_binary_safe",
            lambda _dependencies, values: values.update(
                {module.RESTIC_BINARY: _stat(stat.S_IFREG, 0o777, 0)}
            ),
        ),
        (
            "source_directory_safe",
            lambda _dependencies, values: values.update(
                {module.SOURCE: _stat(stat.S_IFLNK, 0o700, 1000)}
            ),
        ),
        (
            "repository_directory_safe",
            lambda _dependencies, values: values.update(
                {module.REPOSITORY: _stat(stat.S_IFDIR, 0o722, 1000)}
            ),
        ),
        (
            "configuration_metadata_safe",
            lambda _dependencies, values: values.update(
                {module.CONFIG_PATH: _stat(stat.S_IFREG, 0o640, 1000)}
            ),
        ),
        (
            "configuration_content_exact",
            lambda dependencies, _values: dependencies.update(
                config_reader=lambda: "synthetic-nonmatching-content"
            ),
        ),
        (
            "password_file_safe",
            lambda _dependencies, values: values.update(
                {module.PASSWORD_FILE: _stat(stat.S_IFREG, 0o644, 1000)}
            ),
        ),
        (
            "configuration_directory_owner_safe",
            lambda _dependencies, values: values.update(
                {
                    module.CONFIG_DIRECTORY: _stat(
                        stat.S_IFDIR,
                        0o722,
                        1000,
                    )
                }
            ),
        ),
        (
            "configuration_single_link",
            lambda _dependencies, values: values.update(
                {
                    module.CONFIG_PATH: _stat(
                        stat.S_IFREG,
                        0o600,
                        1000,
                        nlink=2,
                    )
                }
            ),
        ),
        (
            "password_regular_single_link",
            lambda _dependencies, values: values.update(
                {
                    module.PASSWORD_FILE: _stat(
                        stat.S_IFREG,
                        0o600,
                        1000,
                        nlink=2,
                    )
                }
            ),
        ),
        (
            "password_owner_repairable",
            lambda _dependencies, values: values.update(
                {
                    module.PASSWORD_FILE: _stat(
                        stat.S_IFREG,
                        0o600,
                        2000,
                    )
                }
            ),
        ),
        (
            "password_nonempty_bounded",
            lambda _dependencies, values: values.update(
                {
                    module.PASSWORD_FILE: _stat(
                        stat.S_IFREG,
                        0o600,
                        1000,
                        size=0,
                    )
                }
            ),
        ),
        (
            "repair_temporary_absent",
            lambda dependencies, _values: dependencies.update(
                path_exists=lambda _path: True
            ),
        ),
        (
            "repair_platform_ready",
            lambda dependencies, _values: dependencies.update(
                platform_checker=lambda: False
            ),
        ),
    ],
)
def test_each_gate_is_distinguished_without_exposing_values(proof, mutate):
    dependencies, values = _dependencies()
    mutate(dependencies, values)
    result = module.collect_backup_configuration_diagnostic(**dependencies)

    assert result["status"] == "observed"
    assert result["contract_ready"] is False
    assert result[proof] is False
    assert module.validate_envelope(result)
    encoded = json.dumps(result)
    assert "synthetic" not in encoded


def test_envelope_rejects_visibility_tamper_and_main_is_fixed(capsys, monkeypatch):
    tampered = module.envelope("blocked", "internal_error")
    tampered["credential_value_visible"] = True
    tampered["evidence_sha256"] = module._digest(tampered)
    assert module.validate_envelope(tampered) is False

    monkeypatch.setattr(
        module,
        "collect_backup_configuration_diagnostic",
        lambda: module.envelope("blocked", "internal_error"),
    )
    assert module.main() == 1
    output = json.loads(capsys.readouterr().out)
    assert module.validate_envelope(output)


def test_envelope_never_serializes_non_boolean_proof_values():
    result = module.envelope(
        "observed",
        "none",
        {"password_file_safe": "synthetic-secret-bearing-value"},
    )

    assert result["password_file_safe"] is False
    assert "synthetic-secret-bearing-value" not in json.dumps(result, sort_keys=True)
    assert module.validate_envelope(result)


def test_safe_existing_private_pointer_is_proven_without_path_disclosure():
    dependencies, values = _dependencies()
    alternate = module.CONFIG_DIRECTORY + "/existing-private-credential"
    values[alternate] = _stat(stat.S_IFREG, 0o600, 1000)
    dependencies["config_reader"] = lambda: (
        "RESTIC_PASSWORD_FILE=" + alternate + "\n"
    )

    result = module.collect_backup_configuration_diagnostic(**dependencies)

    assert result["configuration_content_exact"] is False
    assert result["existing_pointer_contract_ready"] is True
    assert all(
        result[key] is True
        for key in (
            "existing_pointer_syntax_safe",
            "existing_pointer_regular_single_link",
            "existing_pointer_owner_safe",
            "existing_pointer_mode_safe",
            "existing_pointer_nonempty_bounded",
        )
    )
    assert alternate not in json.dumps(result, sort_keys=True)
    assert module.validate_envelope(result)


@pytest.mark.parametrize(
    "content",
    [
        "RESTIC_PASSWORD_COMMAND=synthetic-private-command\n",
        "RESTIC_PASSWORD_FILE=/outside/private-credential\n",
        "RESTIC_PASSWORD_FILE="
        + module.CONFIG_DIRECTORY
        + "/../private-credential\n",
        "RESTIC_PASSWORD_FILE="
        + module.CONFIG_DIRECTORY
        + "/"
        + module.CONFIG_PATH.rsplit("/", 1)[-1]
        + "\n",
    ],
)
def test_unsafe_existing_pointer_forms_are_rejected_without_disclosure(content):
    dependencies, _values = _dependencies()
    dependencies["config_reader"] = lambda: content

    result = module.collect_backup_configuration_diagnostic(**dependencies)

    assert result["existing_pointer_contract_ready"] is False
    assert result["existing_pointer_syntax_safe"] is False
    assert "synthetic-private" not in json.dumps(result, sort_keys=True)
    assert module.validate_envelope(result)
