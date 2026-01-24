import asyncio
import sys

from .cli import build_parser, _run_join


def main() -> None:
    parser = build_parser()
    if any(flag in ("-h", "--help") for flag in sys.argv[1:]):
        parser.print_help()
        return
    args = parser.parse_args(["--join"] + sys.argv[1:])
    asyncio.run(_run_join(args))


if __name__ == "__main__":
    main()
