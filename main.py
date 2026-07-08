#!/usr/bin/env python3
"""Entrypoint minimalista de Sniper AI."""

from dotenv import load_dotenv

from core.config.portable_paths import load_runtime_env

load_runtime_env()
load_dotenv()

from core.bot_app import run_entrypoint  # noqa: E402,I001


if __name__ == "__main__":
    run_entrypoint()
