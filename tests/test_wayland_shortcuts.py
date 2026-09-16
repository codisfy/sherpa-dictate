import asyncio
import os
import sys
import unittest
from unittest.mock import patch

from dbus_next import Message, Variant

from sherpa_app.wayland_shortcuts import (
    SHORTCUT_DEFINITIONS,
    WaylandShortcutPortal,
    is_wayland_session,
    shortcut_ids,
    shortcut_summary,
)


class WaylandShortcutTests(unittest.TestCase):
    def test_detects_wayland_and_ignores_offscreen_tests(self) -> None:
        with patch.object(sys, "platform", "linux"), patch.dict(
            os.environ,
            {"XDG_SESSION_TYPE": "wayland", "WAYLAND_DISPLAY": "wayland-0"},
            clear=True,
        ):
            self.assertTrue(is_wayland_session())
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
            self.assertFalse(is_wayland_session())

    def test_definitions_cover_every_public_action(self) -> None:
        self.assertEqual(
            {item.action for item in SHORTCUT_DEFINITIONS},
            {
                "dictation-toggle",
                "dictation-start",
                "dictation-stop",
                "dictation-manual-toggle",
                "read-selection",
                "tts-stop",
            },
        )

    def test_extracts_ids_and_trigger_summary(self) -> None:
        shortcuts = Variant(
            "a(sa{sv})",
            [
                [
                    "dictation-toggle",
                    {
                        "description": Variant("s", "Toggle dictation"),
                        "trigger_description": Variant("s", "Ctrl+Alt+D"),
                    },
                ],
                [
                    "read-selection",
                    {
                        "description": Variant("s", "Read selection"),
                        "trigger_description": Variant("s", "Ctrl+Alt+R"),
                    },
                ],
            ],
        )
        self.assertEqual(
            shortcut_ids(shortcuts), {"dictation-toggle", "read-selection"}
        )
        self.assertEqual(
            shortcut_summary(shortcuts),
            "2 shortcuts active: Ctrl+Alt+D, Ctrl+Alt+R",
        )

    def test_bind_payload_matches_portal_signature(self) -> None:
        shortcuts = [
            [
                "dictation-toggle",
                {
                    "description": Variant("s", "Toggle continuous dictation"),
                    "preferred_trigger": Variant("s", "CTRL+ALT+d"),
                },
            ]
        ]
        message = Message(
            destination="org.freedesktop.portal.Desktop",
            path="/org/freedesktop/portal/desktop",
            interface="org.freedesktop.portal.GlobalShortcuts",
            member="BindShortcuts",
            signature="oa(sa{sv})sa{sv}",
            body=[
                "/org/freedesktop/portal/desktop/session/test/sherpa_shortcuts",
                shortcuts,
                "",
                {"handle_token": Variant("s", "sherpa_bind_test")},
            ],
        )
        self.assertEqual(message.signature, "oa(sa{sv})sa{sv}")


class PortalShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_dbus_call_is_cancelled_on_shutdown(self) -> None:
        class SlowBus:
            def __init__(self) -> None:
                self.started = asyncio.Event()

            async def call(self, _message: Message) -> Message:
                self.started.set()
                await asyncio.Event().wait()
                raise AssertionError("unreachable")

        portal = WaylandShortcutPortal()
        bus = SlowBus()
        portal._bus = bus  # type: ignore[assignment]
        portal._stop_event = asyncio.Event()
        pending = asyncio.create_task(
            portal._send_message(
                Message(
                    destination="org.freedesktop.portal.Desktop",
                    path="/org/freedesktop/portal/desktop",
                    interface="org.freedesktop.DBus.Properties",
                    member="Get",
                    signature="ss",
                    body=["org.freedesktop.portal.GlobalShortcuts", "version"],
                )
            )
        )
        await bus.started.wait()
        portal._stop_event.set()

        with self.assertRaises(asyncio.CancelledError):
            await pending


if __name__ == "__main__":
    unittest.main()
