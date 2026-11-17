"""Import workspaces with process leases and startup crash recovery."""

from contextlib import ExitStack, contextmanager
import logging
import os
from pathlib import Path
import shutil
import time

from .. import devmode
from ..filelock import file_lock
from ..logging_utils import log_failure
from ..openfiles import remove_tree, temporary_directory
from .import_archive import PREFIX as ARCHIVE_PREFIX

logger = logging.getLogger(__name__)
MIN_IMPORT_AGE = 24 * 60 * 60


def _stale(path: Path, cutoff: float) -> bool:
    if path.stat().st_mtime > cutoff:
        return False
    # Writing an existing output does not update its parent directory's mtime.
    with os.scandir(path) as entries:
        for entry in entries:
            if entry.stat(follow_symlinks=False).st_mtime > cutoff:
                return False
            if entry.is_dir(follow_symlinks=False) and not _stale(Path(entry.path), cutoff):
                return False
    return True


@contextmanager
def import_directory(root: Path):
    """Lease a temporary import workspace and remove it on context exit.

    Args:
        root: Library folder whose staging directory holds the workspace and lease.

    Yields:
        A directory protected from startup cleanup while the context is open.
    """
    staging = root / "staging"
    devmode.check_read(staging)
    devmode.check_write(staging)
    with ExitStack() as resources:
        # Creation and lease acquisition are atomic with respect to startup cleanup.
        with file_lock(staging / ".lock", timeout=5):
            temporary = resources.enter_context(temporary_directory(prefix="import-", dir=staging))
            path = Path(temporary)
            lease = staging / (path.name + ".lock")
            resources.enter_context(file_lock(lease, timeout=0))
        try:
            yield path
        finally:
            with file_lock(staging / ".lock", timeout=5):
                resources.close()
                lease.unlink(missing_ok=True)


def cleanup(root: Path) -> None:
    """Remove abandoned import workspaces with no writes in the last day.

    Args:
        root: Library folder whose staging directory is checked for inactive leases.
    """
    staging = root / "staging"
    if staging.is_symlink() or not staging.is_dir():
        return
    try:
        devmode.check_read(staging)
        devmode.check_write(staging)
        with file_lock(staging / ".lock", timeout=0):
            for path in staging.glob("import-*"):
                if path.is_symlink() or not path.is_dir():
                    continue
                lease = staging / (path.name + ".lock")
                if lease.is_symlink():
                    continue
                try:
                    with file_lock(lease, timeout=0):
                        # Lease-free workspaces also need protection from recent activity.
                        if not _stale(path, time.time() - MIN_IMPORT_AGE):
                            continue
                        shutil.rmtree(path)
                    lease.unlink(missing_ok=True)
                except TimeoutError:
                    continue  # Another instance is still using this workspace.
                except OSError as error:
                    log_failure(logger, logging.WARNING, "Could not remove abandoned import", error)
            # Unpacked archives (import_archive) have no lease: a startup keeps those used within the last day.
            for path in staging.glob(ARCHIVE_PREFIX + "*"):
                if path.is_symlink() or not path.is_dir():
                    continue
                try:
                    if _stale(path, time.time() - MIN_IMPORT_AGE):
                        remove_tree(path)
                except OSError as error:
                    log_failure(logger, logging.WARNING, "Could not remove an unpacked archive", error)
    except TimeoutError:
        pass  # A creation or another cleanup currently owns the directory list.
    except OSError as error:
        log_failure(logger, logging.WARNING, "Could not clean import staging", error)
