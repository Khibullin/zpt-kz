"""Return idle allocator pages on small Linux instances, at safe job boundaries."""
import ctypes
import gc
import os
import threading
import time
from pathlib import Path

MIB = 1024 * 1024
_lock = threading.Lock()
_last_release = 0.0


def idle_work_has_headroom():
    """Keep 160 MiB free before optional public-cache rebuilding on Render."""
    try:
        limit = Path('/sys/fs/cgroup/memory.max').read_text().strip()
        if limit == 'max':
            return True
        used = int(Path('/sys/fs/cgroup/memory.current').read_text())
        return used + 160 * MIB < int(limit)
    except (OSError, ValueError):
        # Other platforms do not expose this Linux cgroup interface.
        return True


def resident_bytes():
    try:
        return int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError, IndexError):
        return 0


def release_idle_memory():
    """Never evict app data or stop a task; collect only unreachable objects.

    glibc otherwise retains freed large JSON/image allocations for the lifetime
    of the worker. Throttle collection across request threads, and safely skip
    the optional native operation on platforms without malloc_trim.
    """
    global _last_release
    before = resident_bytes()
    if before < 128 * MIB or not _lock.acquire(blocking=False):
        return before
    try:
        now = time.monotonic()
        if now - _last_release < 30:
            return before
        _last_release = now
        gc.collect()
        try:
            trim = ctypes.CDLL(None).malloc_trim
            trim.argtypes = [ctypes.c_size_t]
            trim.restype = ctypes.c_int
            trim(0)
        except (AttributeError, OSError):
            pass
        return resident_bytes()
    finally:
        _lock.release()
