"""Package launcher: GUI with no arguments, CLI when arguments are supplied."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or "--gui" in args:
        from .gui import main as gui_main

        gui_main()
        return 0
    from .cli import main as cli_main

    return cli_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
