import asyncio
import sys

from .cli import build_parser, _run_create_party


def main() -> None:
    parser = build_parser()
    if any(flag in ("-h", "--help") for flag in sys.argv[1:]):
        parser.print_help()
        return
    args = parser.parse_args(["--create-party"] + sys.argv[1:])
    asyncio.run(_run_create_party(args))


if __name__ == "__main__":
    main()
