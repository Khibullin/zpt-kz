"""Isolated settings for read-only UI regression tests in GitHub Actions.

The production schema and migration history are unchanged. Tests use a fresh
temporary SQLite schema without executing data-dependent historical migrations.
"""
from .settings import *  # noqa: F403,F401

DATABASES["default"]["TEST"] = {"MIGRATE": False}
