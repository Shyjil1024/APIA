"""Core-feature tests for the greet_cli CLI (models live-generated tests)."""

from greet_cli.__main__ import greet, main


def test_greet_default():
    assert greet() == "Hello, world!"


def test_greet_named():
    assert greet("Ada") == "Hello, Ada!"


def test_greet_shout():
    assert greet("Ada", shout=True) == "HELLO, ADA!"


def test_main_parses_name_and_flag():
    assert main(["Ada", "--shout"]) == "HELLO, ADA!"
