from __future__ import annotations

import stat
from pathlib import Path

import pytest

from muzilla.services.secrets import FileSecretStore, SecretStoreError


def test_file_secret_store_persists_with_owner_only_permissions(tmp_path: Path) -> None:
    root = tmp_path / "provider-secrets"
    reference = "providers.discogs.token"
    store = FileSecretStore(root)
    store.set(reference, "synthetic-test-token")

    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    secret_files = list(root.glob("*.secret"))
    assert len(secret_files) == 1
    assert stat.S_IMODE(secret_files[0].stat().st_mode) == 0o600
    assert FileSecretStore(root).get(reference) == "synthetic-test-token"


def test_file_secret_store_rejects_symlink_root(tmp_path: Path) -> None:
    authority = tmp_path / "authority"
    authority.mkdir()
    symlink = tmp_path / "provider-secrets"
    symlink.symlink_to(authority, target_is_directory=True)

    with pytest.raises(SecretStoreError, match="not a directory"):
        FileSecretStore(symlink)


def test_file_secret_store_rejects_unsafe_secret_permissions(tmp_path: Path) -> None:
    store = FileSecretStore(tmp_path / "provider-secrets")
    store.set("providers.discogs.token", "synthetic-test-token")
    secret_file = next(store.root.glob("*.secret"))
    secret_file.chmod(0o640)

    with pytest.raises(SecretStoreError, match="unsafe permissions"):
        store.get("providers.discogs.token")


def test_clear_rejects_digest_shaped_but_unmanaged_name_before_unlinking(
    tmp_path: Path,
) -> None:
    store = FileSecretStore(tmp_path / "provider-secrets")
    reference = "providers.discogs.token"
    store.set(reference, "synthetic-test-token")
    unmanaged = store.root / f"{'z' * 64}.secret"
    unmanaged.write_bytes(b"not-owned-by-the-secret-store")
    unmanaged.chmod(0o600)

    with pytest.raises(SecretStoreError, match="unmanaged entry"):
        store.clear()

    assert store.get(reference) == "synthetic-test-token"
    assert unmanaged.exists()


@pytest.mark.parametrize("reference", ["../escape", "providers/discogs", "UPPERCASE", ""])
def test_file_secret_store_rejects_invalid_references(tmp_path: Path, reference: str) -> None:
    store = FileSecretStore(tmp_path / "provider-secrets")

    with pytest.raises(SecretStoreError, match="invalid secret reference"):
        store.set(reference, "synthetic-test-token")
