"""Entrypoint for the greet_cli CLI (APIA skeleton + core feature).

The skeleton (the runnable ``main()`` + the ``python -m greet_cli`` entrypoint)
is emitted deterministically by scaffold.py. The small ``greet()`` core feature
below models the LIVE Copilot generation step: a minimal, idiomatic argparse
CLI so "it actually runs" is a meaningful claim, not an empty stub.
"""

import argparse


def greet(name="world", shout=False):
    """Return a greeting for ``name`` (uppercased when ``shout`` is set)."""
    message = f"Hello, {name}!"
    return message.upper() if shout else message


def main(argv=None):
    """Parse argv and return the CLI result string.

    Returning the string (rather than only printing) keeps the entrypoint
    unit-testable while the ``__main__`` guard still prints it for real use.
    """
    parser = argparse.ArgumentParser(
        prog="greet_cli", description="Greet a user."
    )
    parser.add_argument(
        "name", nargs="?", default="world",
        help="who to greet (default: world)"
    )
    parser.add_argument(
        "--shout", action="store_true", help="uppercase the greeting"
    )
    args = parser.parse_args(argv)
    return greet(args.name, shout=args.shout)


if __name__ == "__main__":  # pragma: no cover - exercised via -m in real use
    print(main())
