import os
import sys

try:
    import fcntl  # POSIX
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt  # Windows
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None  # type: ignore[assignment]

_single_instance_lock = None


def _try_acquire_lock(lock_file) -> bool:
    if fcntl is not None:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False

    if msvcrt is not None:
        try:
            lock_file.seek(0)
            if lock_file.tell() == 0 and lock_file.read(1) == "":
                lock_file.seek(0)
                lock_file.write("0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    return False


def acquire_single_instance_lock(logger, lock_filename: str = ".sniperai.lock") -> bool:
    global _single_instance_lock

    lock_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), lock_filename)
    lock_file = open(lock_path, "a+", encoding="utf-8")

    if not _try_acquire_lock(lock_file):
        lock_file.seek(0)
        owner = lock_file.read().strip() or "desconocido"
        msg = (
            f"🚫 Ya existe otra instancia ejecutándose (lock owner: {owner}). "
            "Abortando para proteger API/riesgo operativo."
        )
        logger.error(msg)
        print(msg, file=sys.stderr)
        lock_file.close()
        return False

    lock_file.seek(0)
    lock_file.truncate()
    lock_file.write(str(os.getpid()))
    lock_file.flush()
    _single_instance_lock = lock_file
    return True
