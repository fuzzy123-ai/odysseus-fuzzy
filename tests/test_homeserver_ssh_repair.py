from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ops" / "homeserver" / "repair-ssh-access.sh"


def test_ssh_repair_uses_existing_public_key_and_homebase_by_default():
    script = SCRIPT.read_text(encoding="utf-8")

    assert "odysseus-homeserver-20260620.pub" in script
    assert "ODYSSEUS_SSH_REPAIR_USER:-homebase" in script
    assert "authorized_keys" in script
    assert "cat \"$pubkey_path\"" in script
    assert "cat \"$pubkey_path\" | sudo tee -a" in script
    assert "cat \"$pubkey_path\" | sudo tee" in script


def test_ssh_repair_starts_only_ssh_and_opens_only_openssh_firewall_rule():
    script = SCRIPT.read_text(encoding="utf-8")
    lower = script.lower()

    assert "openssh-server" in script
    assert "sudo sshd -t" in script
    assert "enable --now ssh.service" in script
    assert "enable --now sshd.service" in script
    assert "sudo ufw allow OpenSSH" in script
    assert "ufw allow 22" not in script
    assert "ufw disable" not in lower
    assert "systemctl restart" not in lower
    assert "podman" not in lower
    assert "docker" not in lower


def test_ssh_repair_does_not_embed_private_keys_or_secrets():
    script = SCRIPT.read_text(encoding="utf-8")
    lower = script.lower()

    assert "id_ed25519" not in script
    assert "private key" not in lower
    assert "token" not in lower
    assert "password" not in lower
    assert "passwd" not in lower
