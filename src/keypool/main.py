"""Console script entry point for KEYPOOL (`keypool = "keypool.main:app"`)."""

from __future__ import annotations

from keypool.cli.app import app

__all__ = ["app"]


def run() -> None:
    """Invoke the KEYPOOL Typer application."""
    app()


if __name__ == "__main__":
    run()
