from greet_cli.__main__ import main


def test_main_runs():
    # Smoke test: the generated entrypoint is importable and runs with no args.
    assert main([]) == "Hello, world!"
