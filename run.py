# entry point
"""
Echo entry point.

    python run.py            # interactive REPL
    python run.py --demo     # scripted requests, no typing needed

Makes `src/` importable so `from echo.config import settings` works without
installing the package.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from echo.agent import handle  # noqa: E402
from echo.config import settings  # noqa: E402
from echo.db import init_db  # noqa: E402

DEMO = [
    "Remind me to call the plumber tomorrow at 9am.",
    "Turn on the living room light.",
    "What's on my calendar?",
    "Turn off the bedroom fan and remind me to water the plants tonight at 7.",
]


def main() -> None:
    init_db()  # create tables if they don't exist
    print(f"Echo — model={settings.model}  db=postgres\n")
    history: list = []

    if "--demo" in sys.argv:
        for line in DEMO:
            print(f"you > {line}")
            print(f"echo> {handle(line, history)}\n")
        return

    print("Text mode. Ctrl-C to quit.\n")
    while True:
        try:
            line = input("you > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if line:
            print(f"echo> {handle(line, history)}")


if __name__ == "__main__":
    main()