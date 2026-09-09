"""Atomic, no-clobber file output shared by all report writers."""

from __future__ import annotations

import os
import tempfile
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from regressistor.errors import OutputError


def _identity(path: Path) -> str:
    return unicodedata.normalize("NFC", str(path.resolve(strict=False))).casefold()


def _aliases(first: Path, second: Path) -> bool:
    try:
        return _identity(first) == _identity(second) or (
            first.exists() and second.exists() and first.samefile(second)
        )
    except OSError as error:
        raise OutputError(f"cannot resolve output path alias: {error}") from error


def preflight_output(
    destination: str | Path,
    *,
    context: str,
    force: bool,
    protected: Iterable[str | Path] = (),
) -> Path:
    """Reject an input alias or an existing destination before any write begins."""

    target = Path(destination)
    if any(_aliases(target, Path(source)) for source in protected):
        raise OutputError(f"{context} {target} aliases an input and is never writable")
    if target.exists() and not force:
        raise OutputError(f"refusing to overwrite existing {context}: {target}")
    return target


def atomic_write_bytes(
    destination: str | Path,
    payload: bytes,
    *,
    context: str,
    force: bool = False,
    protected: Iterable[str | Path] = (),
) -> Path:
    """Flush and atomically install one complete byte payload."""

    target = preflight_output(
        destination,
        context=context,
        force=force,
        protected=protected,
    )
    temporary: Path | None = None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if force:
            os.replace(temporary, target)
        else:
            try:
                os.link(temporary, target)
            except FileExistsError as error:
                raise OutputError(f"refusing to overwrite existing {context}: {target}") from error
        temporary.unlink(missing_ok=True)
        temporary = None
    except OutputError:
        raise
    except OSError as error:
        raise OutputError(f"cannot write {context} {target}: {error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def _stage_payload(target: Path, payload: bytes) -> Path:
    if type(payload) is not bytes:
        raise OutputError("transaction payloads must be bytes")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f".{target.name}.",
            suffix=".tmp",
            dir=target.parent,
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return temporary
    except OSError as error:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        raise OutputError(f"cannot stage transaction output {target}: {error}") from error


def _reserved_recovery_path(target: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".rollback", dir=target.parent
    )
    os.close(descriptor)
    return Path(name)


def _file_identity(path: Path) -> tuple[int, int]:
    stat = path.stat(follow_symlinks=False)
    return stat.st_dev, stat.st_ino


def _file_claim(path: Path) -> tuple[int, int, str]:
    device, inode = _file_identity(path)
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return device, inode, digest.hexdigest()


@dataclass(frozen=True, slots=True)
class _Backup:
    path: Path
    original_claim: tuple[int, int, str]
    placeholder_claim: tuple[int, int, str]


def _claim_state(path: Path) -> tuple[str, tuple[int, int, str] | None]:
    try:
        return "present", _file_claim(path)
    except FileNotFoundError:
        return "absent", None
    except OSError:
        return "unknown", None


def _restore_no_clobber(recovery: Path, target: Path) -> tuple[bool, bool]:
    try:
        os.link(recovery, target, follow_symlinks=False)
    except BaseException:
        return False, True
    try:
        recovery.unlink()
    except BaseException:
        return True, True
    return True, False


def _remove_if_owned(target: Path, claim: tuple[int, int, str]) -> str | None:
    """Quarantine a published entry before deciding whether it is ours to remove."""

    recovery = _reserved_recovery_path(target)
    placeholder_claim = _file_claim(recovery)
    try:
        os.replace(target, recovery)
    except FileNotFoundError:
        state, current = _claim_state(recovery)
        if state == "present" and current == placeholder_claim:
            try:
                recovery.unlink()
            except OSError:
                return f"{target} (unused recovery retained at {recovery})"
        return None
    except BaseException:
        state, current = _claim_state(recovery)
        if state != "present" or current != claim:
            if state == "present" and current == placeholder_claim:
                try:
                    recovery.unlink()
                except OSError:
                    return f"{target} (unused recovery retained at {recovery})"
            return f"{target} (entry retained after interrupted quarantine)"
        # The OS move completed before the asynchronous exception was raised.
        # Continue from the known quarantine path instead of losing ownership.
    try:
        if _file_claim(recovery) == claim:
            recovery.unlink()
            return None
    except OSError:
        return f"{target} (unreadable entry retained at {recovery})"
    try:
        restored, retained = _restore_no_clobber(recovery, target)
    except BaseException:
        return f"{target} (concurrent content retained at {recovery})"
    if restored and retained:
        return f"{target} (concurrent content restored; recovery retained at {recovery})"
    if restored:
        return f"{target} (concurrent content restored)"
    return f"{target} (concurrent content retained at {recovery})"


def _restore_backup(target: Path, backup: _Backup) -> str | None:
    target_state, target_claim = _claim_state(target)
    backup_state, backup_claim = _claim_state(backup.path)
    if target_state == "present" and target_claim == backup.original_claim:
        if backup_state == "absent":
            return None
        if backup_state == "present" and backup_claim == backup.placeholder_claim:
            try:
                backup.path.unlink()
            except BaseException:
                return f"{target} (unused recovery retained at {backup.path})"
            return None
    if (
        target_state == "absent"
        and backup_state == "present"
        and (backup_claim == backup.original_claim)
    ):
        try:
            os.replace(backup.path, target)
        except BaseException:
            target_state, target_claim = _claim_state(target)
            backup_state, _backup_claim = _claim_state(backup.path)
            if (
                target_state == "present"
                and target_claim == backup.original_claim
                and backup_state == "absent"
            ):
                return None
            return f"{target} (original retained at {backup.path})"
        return None
    return f"{target} (original retained at {backup.path})"


def _install_staged(staged: Path, target: Path, *, force: bool) -> None:
    """Install one staged entry; isolated for deterministic fault-injection tests."""

    if force:
        os.replace(staged, target)
        return
    try:
        os.link(staged, target)
    except FileExistsError as error:
        raise OutputError(f"refusing to overwrite existing transaction output: {target}") from error


def atomic_write_many(
    outputs: Iterable[tuple[str | Path, bytes]],
    *,
    context: str,
    force: bool = False,
    protected: Iterable[str | Path] = (),
) -> tuple[Path, ...]:
    """Publish a set of complete files or roll the entire set back.

    Every payload is flushed before installation. No-clobber installs use hard
    links, so a competing creator wins without being overwritten. Force mode
    first moves existing entries to private sibling backups. On failure, files
    still owned by this transaction are removed and originals are restored.
    Concurrent replacements are never deleted during rollback; displaced
    originals are retained as ``.rollback`` files and named in the error.
    """

    items = tuple((Path(destination), payload) for destination, payload in outputs)
    if not items:
        raise OutputError(f"{context} transaction must contain at least one output")
    identities = [_identity(target) for target, _payload in items]
    if len(set(identities)) != len(identities):
        raise OutputError(f"{context} transaction destinations must be unique")
    protected_items = tuple(protected)
    for target, _payload in items:
        preflight_output(
            target,
            context=context,
            force=force,
            protected=protected_items,
        )

    staged: dict[Path, Path] = {}
    backups: dict[Path, _Backup] = {}
    installed: dict[Path, tuple[int, int, str]] = {}
    confirmed: set[Path] = set()
    preserve_backups = False
    try:
        for target, payload in items:
            staged[target] = _stage_payload(target, payload)
        if force:
            for target, _payload in items:
                if target.exists() or target.is_symlink():
                    original_claim = _file_claim(target)
                    backup_path = _reserved_recovery_path(target)
                    backup = _Backup(
                        backup_path,
                        original_claim,
                        _file_claim(backup_path),
                    )
                    backups[target] = backup
                    os.replace(target, backup_path)
        for target, _payload in items:
            temporary = staged[target]
            staged_identity = _file_claim(temporary)
            # Register a tentative content claim before the syscall. Rollback
            # removes it only if the public path later matches this exact
            # inode and payload, which closes the post-syscall signal window
            # without risking a competing creator's bytes.
            installed[target] = staged_identity
            _install_staged(temporary, target, force=force)
            confirmed.add(target)
            if force:
                staged.pop(target)
        for backup in backups.values():
            backup.path.unlink(missing_ok=True)
        backups.clear()
    except BaseException as error:
        # From this point onward, every displaced original is recovery data.
        # Never let a secondary rollback failure make finally delete it.
        preserve_backups = bool(backups)
        if len(confirmed) == len(items):
            # Publication completed; a backup cleanup failure must not trigger
            # a destructive rollback of the already-consistent new set.
            preserve_backups = True
            remaining = ", ".join(
                str(backup.path) for backup in backups.values() if backup.path.exists()
            )
            raise OutputError(
                f"published {context} transaction but could not remove rollback backup: "
                f"{remaining or error}"
            ) from error
        rollback_conflicts: list[str] = []
        for target, owned_identity in reversed(tuple(installed.items())):
            conflict = _remove_if_owned(target, owned_identity)
            if conflict is not None:
                rollback_conflicts.append(conflict)
        for target, backup in reversed(tuple(backups.items())):
            conflict = _restore_backup(target, backup)
            if conflict is None:
                backups.pop(target)
            else:
                rollback_conflicts.append(conflict)
                preserve_backups = True
        if rollback_conflicts:
            locations = ", ".join(rollback_conflicts)
            raise OutputError(
                f"cannot publish {context} transaction and rollback needs recovery: {locations}"
            ) from error
        if not isinstance(error, (OSError, OutputError)):
            raise
        if isinstance(error, OutputError):
            raise
        raise OutputError(f"cannot publish {context} transaction: {error}") from error
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)
        if not preserve_backups:
            for backup in backups.values():
                backup.path.unlink(missing_ok=True)
    return tuple(target for target, _payload in items)
