#!/usr/bin/env python3
"""Single entry point for the Sherpa GUI, engines, and shortcut actions."""

from __future__ import annotations

import sys
from collections.abc import Sequence


ACTION_COMMANDS = {
    "dictation-toggle": ("dictate", ["continuous"]),
    "dictation-start": ("dictate", ["continuous-start"]),
    "dictation-stop": ("dictate", ["stop"]),
    "dictation-manual-toggle": ("dictate", ["toggle"]),
    "read-selection": ("read", ["selection"]),
    "tts-stop": ("read", ["stop"]),
}


def _run_engine(engine: str, arguments: Sequence[str]) -> int:
    if engine == "dictate":
        from sherpa_dictate import client_main, parse_arguments

        return client_main(parse_arguments(list(arguments)))
    if engine == "read":
        from sherpa_read import client_main, parse_arguments

        return client_main(parse_arguments(list(arguments)))
    print(f"Unknown Sherpa engine: {engine}", file=sys.stderr)
    return 2


def _print_help() -> None:
    print(
        """Usage:
  sherpa                         Open the desktop app
  sherpa --minimized             Start in the system tray
  sherpa action ACTION           Run a keyboard-shortcut action

Actions:
  dictation-toggle               Toggle continuous dictation
  dictation-start                Start continuous dictation
  dictation-stop                 Stop dictation
  dictation-manual-toggle        Toggle record-then-transcribe mode
  read-selection                 Read the selected text
  tts-stop                       Stop text to speech
"""
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments or arguments == ["--minimized"]:
        from sherpa_app.main import main as gui_main

        return gui_main(arguments)

    if arguments[0] in {"-h", "--help", "help"}:
        _print_help()
        return 0

    if arguments[0] == "action":
        if len(arguments) != 2 or arguments[1] not in ACTION_COMMANDS:
            _print_help()
            return 2
        engine, engine_arguments = ACTION_COMMANDS[arguments[1]]
        return _run_engine(engine, engine_arguments)

    # Private entry used when the bundled executable starts a background daemon.
    if arguments[0] == "engine" and len(arguments) >= 3:
        return _run_engine(arguments[1], arguments[2:])

    _print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
