import sys
from typing import Any


def debug_print(*args: Any, **kwargs: Any) -> None:
    """Print debug logs with a consistent prefix and immediate flush."""
    file = kwargs.pop("file", sys.stderr)
    flush = kwargs.pop("flush", True)
    print("[DEBUG]", *args, file=file, flush=flush, **kwargs)
