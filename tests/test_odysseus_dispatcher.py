from tests.helpers.cli_loader import load_script


def test_is_runnable_subcommand_requires_executable_file(tmp_path, monkeypatch):
    cli = load_script("odysseus")
    sub = tmp_path / "odysseus-demo"
    sub.write_text("#!/bin/sh\n")

    monkeypatch.setattr(cli.os, "access", lambda path, mode: False)
    assert cli._is_runnable_subcommand(sub) is False

    monkeypatch.setattr(cli.os, "access", lambda path, mode: mode == cli.os.X_OK)
    assert cli._is_runnable_subcommand(sub) is True
    assert cli._is_runnable_subcommand(tmp_path) is False
