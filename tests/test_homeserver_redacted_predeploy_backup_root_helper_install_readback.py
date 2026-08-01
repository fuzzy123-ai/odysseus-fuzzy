from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import stat

from ops.homeserver import redacted_predeploy_backup_root_helper_install_readback as subject


def _good(monkeypatch) -> None:
    monkeypatch.setattr(subject, "_asset_valid", lambda *args, **kwargs: True)
    monkeypatch.setattr(subject, "_parents_valid", lambda **kwargs: True)
    monkeypatch.setattr(subject, "_state_safe_and_arm_absent", lambda **kwargs: (True, False))
    monkeypatch.setattr(subject, "_systemctl_state", lambda *args, **kwargs: True)


def test_positive_readback_is_strictly_redacted(monkeypatch) -> None:
    _good(monkeypatch)
    value = subject.collect()
    assert subject.validate(value) and value["status"] == "available" and value["arm_present"] is False


def test_every_asset_or_state_failure_is_unknown(monkeypatch) -> None:
    _good(monkeypatch)
    monkeypatch.setattr(subject, "_asset_valid", lambda *args, **kwargs: False)
    assert subject.collect()["status"] == "unknown"  # symlink, hardlink, owner, mode, or hash rejection
    _good(monkeypatch); monkeypatch.setattr(subject, "_parents_valid", lambda **kwargs: False)
    assert subject.collect()["status"] == "unknown"
    _good(monkeypatch); monkeypatch.setattr(subject, "_state_safe_and_arm_absent", lambda **kwargs: (False, True))
    value = subject.collect(); assert value["status"] == "unknown" and value["arm_present"] is True
    _good(monkeypatch); monkeypatch.setattr(subject, "_systemctl_state", lambda argument, **kwargs: argument == "is-enabled")
    assert subject.collect()["status"] == "unknown"


def test_readback_has_descriptor_walk_exact_four_asset_pins_and_bounded_systemctl() -> None:
    assert len(subject.ASSETS) == 4
    text = Path(subject.__file__).read_text(encoding="utf-8")
    assert "O_NOFOLLOW" in text and "dir_fd=current" in text and "before.st_nlink == 1" in text
    assert all(field in text for field in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "api.read(fd, 1) == b\"\""))
    assert "hashlib.sha256" in text and '"/usr/bin/systemctl"' in text and "stderr=subprocess.DEVNULL" in text
    assert "disabled\\n" in text and "static\\n" in text and "inactive\\n" in text and "arm.json" in text


def test_validator_rejects_tampering(monkeypatch) -> None:
    _good(monkeypatch)
    value = subject.collect(); value["unit_inactive"] = False; value["evidence_sha256"] = subject._digest(value)
    assert not subject.validate(value)


def test_state_directory_is_validated_after_nofollow_open_and_rejects_opened_unsafe_dir(monkeypatch) -> None:
    monkeypatch.setattr(subject, "_open_parent", lambda *args, **kwargs: (41, "state"))
    class Unsafe:
        def open(self, *args, **kwargs): return 42
        def fstat(self, descriptor): return SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0, st_gid=99, st_nlink=2, st_dev=1, st_ino=2, st_mtime_ns=3, st_ctime_ns=4)
        def close(self, descriptor): pass
    assert subject._state_safe_and_arm_absent(api=Unsafe()) == (False, True)
