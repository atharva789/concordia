import sys
import os
from typing import Any

GREY = "\033[90m"
RESET = "\033[0m"


def debug_print(*args: Any, **kwargs: Any) -> None:
    """Print debug logs with a consistent prefix and immediate flush."""
    if os.environ.get("CONCORDIA_DEBUG", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    file = kwargs.pop("file", sys.stderr)
    flush = kwargs.pop("flush", True)
    print(f"{GREY}[DEBUG]", *args, f"{RESET}", file=file, flush=flush, **kwargs)
