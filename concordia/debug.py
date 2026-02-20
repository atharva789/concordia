import sys
from typing import Any

GREY = "\033[90m"
RESET = "\033[0m"


def debug_print(*args: Any, **kwargs: Any) -> None:
    """Print debug logs with a consistent prefix and immediate flush."""
    file = kwargs.pop("file", sys.stderr)
    flush = kwargs.pop("flush", True)
    print(f"{GREY}[DEBUG]", *args, f"{RESET}", file=file, flush=flush, **kwargs)
