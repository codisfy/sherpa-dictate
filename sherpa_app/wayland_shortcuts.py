"""Wayland global shortcuts backed by the XDG Desktop Portal.

The portal owns the actual key bindings.  Sherpa keeps a session open while the
tray application is running and translates portal activations into app actions.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import uuid
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QThread, Signal

from dbus_next import BusType, Message, MessageType, Variant
from dbus_next.aio import MessageBus


PORTAL_BUS_NAME = "org.freedesktop.portal.Desktop"
PORTAL_OBJECT_PATH = "/org/freedesktop/portal/desktop"
APP_ID = "io.sherpa.Sherpa"
SHORTCUTS_INTERFACE = "org.freedesktop.portal.GlobalShortcuts"
REGISTRY_INTERFACE = "org.freedesktop.host.portal.Registry"
REQUEST_INTERFACE = "org.freedesktop.portal.Request"
SESSION_INTERFACE = "org.freedesktop.portal.Session"
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
DBUS_INTERFACE = "org.freedesktop.DBus"
DBUS_OBJECT_PATH = "/org/freedesktop/DBus"


@dataclass(frozen=True)
class ShortcutDefinition:
    action: str
    description: str
    preferred_trigger: str | None = None


SHORTCUT_DEFINITIONS = (
    ShortcutDefinition(
        "dictation-toggle", "Toggle continuous dictation", "CTRL+ALT+d"
    ),
    ShortcutDefinition("dictation-start", "Start continuous dictation"),
    ShortcutDefinition("dictation-stop", "Stop dictation"),
    ShortcutDefinition(
        "dictation-manual-toggle", "Toggle manual dictation", "CTRL+ALT+m"
    ),
    ShortcutDefinition("read-selection", "Read selected text", "CTRL+ALT+r"),
    ShortcutDefinition("tts-stop", "Stop text to speech"),
)


def is_wayland_session() -> bool:
    """Return whether native portal registration should be attempted."""

    if not sys.platform.startswith("linux"):
        return False
    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
        return False
    return bool(os.environ.get("WAYLAND_DISPLAY")) or (
        os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
    )


def shortcut_ids(value: Any) -> set[str]:
    """Extract shortcut IDs from a portal a(sa{sv}) value."""

    if isinstance(value, Variant):
        value = value.value
    if not isinstance(value, (list, tuple)):
        return set()
    return {
        str(entry[0])
        for entry in value
        if isinstance(entry, (list, tuple)) and entry
    }


def shortcut_summary(value: Any) -> str:
    """Produce a compact human-readable summary of active bindings."""

    if isinstance(value, Variant):
        value = value.value
    descriptions: list[str] = []
    if isinstance(value, (list, tuple)):
        for entry in value:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            details = entry[1]
            trigger = (
                details.get("trigger_description")
                if isinstance(details, dict)
                else None
            )
            if isinstance(trigger, Variant):
                trigger = trigger.value
            if trigger:
                descriptions.append(str(trigger))
    if not descriptions:
        return "No key combinations are assigned yet."
    noun = "shortcuts" if len(descriptions) != 1 else "shortcut"
    return f"{len(descriptions)} {noun} active: " + ", ".join(descriptions)


class PortalError(RuntimeError):
    pass


class WaylandShortcutPortal(QThread):
    """Own a portal session on a background asyncio/D-Bus thread."""

    status_changed = Signal(str, bool)
    shortcut_activated = Signal(str)

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._bus: MessageBus | None = None
        self._session_handle = ""
        self._version = 0
        self._responses: dict[str, tuple[int, dict[str, Any]]] = {}
        self._response_waiters: dict[
            str, asyncio.Future[tuple[int, dict[str, Any]]]
        ] = {}
        self._configure_in_progress = False
        self._stop_requested = threading.Event()

    def stop(self) -> None:
        self._stop_requested.set()
        loop = self._loop
        event = self._stop_event
        if loop is not None and event is not None:
            loop.call_soon_threadsafe(event.set)

    def configure_shortcuts(self) -> None:
        loop = self._loop
        if loop is None or not loop.is_running():
            self.status_changed.emit("Shortcut service is not running.", False)
            return
        loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self._configure_shortcuts())
        )

    def run(self) -> None:
        try:
            asyncio.run(self._run_portal())
        except BaseException as error:
            if not isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)):
                self.status_changed.emit(
                    f"Wayland shortcut registration failed: {error}", False
                )

    async def _run_portal(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        if self._stop_requested.is_set():
            self._stop_event.set()
        self.status_changed.emit("Connecting to the Wayland shortcut portal…", False)
        try:
            self._bus = await MessageBus(bus_type=BusType.SESSION).connect()
            self._bus.add_message_handler(self._handle_message)
            await self._install_signal_matches()
            await self._register_host_app()
            self._version = await self._portal_version()
            await self._create_session()
            await self._restore_or_bind_shortcuts()
            await self._stop_event.wait()
        finally:
            await self._close_session()
            if self._bus is not None:
                self._bus.disconnect()
            self._bus = None
            self._loop = None

    async def _install_signal_matches(self) -> None:
        rules = (
            f"type='signal',interface='{REQUEST_INTERFACE}',member='Response'",
            f"type='signal',interface='{SHORTCUTS_INTERFACE}'",
            f"type='signal',interface='{SESSION_INTERFACE}',member='Closed'",
        )
        for rule in rules:
            await self._call(
                Message(
                    destination=DBUS_INTERFACE,
                    path=DBUS_OBJECT_PATH,
                    interface=DBUS_INTERFACE,
                    member="AddMatch",
                    signature="s",
                    body=[rule],
                )
            )

    async def _portal_version(self) -> int:
        reply = await self._call(
            Message(
                destination=PORTAL_BUS_NAME,
                path=PORTAL_OBJECT_PATH,
                interface=PROPERTIES_INTERFACE,
                member="Get",
                signature="ss",
                body=[SHORTCUTS_INTERFACE, "version"],
            )
        )
        if not reply.body or not isinstance(reply.body[0], Variant):
            raise PortalError("the desktop does not expose GlobalShortcuts")
        return int(reply.body[0].value)

    async def _register_host_app(self) -> None:
        """Give host portals a stable identity when the interface is available."""

        if self._bus is None:
            raise PortalError("D-Bus is not connected")
        reply = await self._send_message(
            Message(
                destination=PORTAL_BUS_NAME,
                path=PORTAL_OBJECT_PATH,
                interface=REGISTRY_INTERFACE,
                member="Register",
                signature="sa{sv}",
                body=[APP_ID, {}],
            )
        )
        if reply.message_type != MessageType.ERROR:
            return
        name = reply.error_name or ""
        detail = str(reply.body[0]) if reply.body else name
        expected_failures = (
            "UnknownInterface",
            "UnknownMethod",
            "already associated",
            "sandbox",
        )
        if any(
            value.lower() in f"{name} {detail}".lower()
            for value in expected_failures
        ):
            return
        raise PortalError(
            f"could not register {APP_ID}: {detail}. Install Sherpa's desktop "
            "launcher before using automatic shortcuts"
        )

    async def _create_session(self) -> None:
        response, results = await self._portal_request(
            "CreateSession",
            "a{sv}",
            [
                {
                    "handle_token": Variant("s", self._token("create")),
                    "session_handle_token": Variant("s", "sherpa_shortcuts"),
                }
            ],
        )
        if response != 0:
            raise PortalError("the desktop declined the shortcut session")
        handle = results.get("session_handle")
        if isinstance(handle, Variant):
            handle = handle.value
        if not isinstance(handle, str) or not handle.startswith("/"):
            raise PortalError("the desktop returned an invalid shortcut session")
        self._session_handle = handle

    async def _restore_or_bind_shortcuts(self) -> None:
        response, results = await self._portal_request(
            "ListShortcuts",
            "oa{sv}",
            [self._session_handle, {"handle_token": Variant("s", self._token("list"))}],
        )
        if response != 0:
            raise PortalError("the desktop could not list shortcuts")

        current = results.get("shortcuts", [])
        existing = shortcut_ids(current)
        missing = [item for item in SHORTCUT_DEFINITIONS if item.action not in existing]
        if not missing:
            self._emit_binding_status(current)
            return

        shortcuts: list[list[Any]] = []
        for item in missing:
            properties = {"description": Variant("s", item.description)}
            if item.preferred_trigger:
                properties["preferred_trigger"] = Variant(
                    "s", item.preferred_trigger
                )
            shortcuts.append([item.action, properties])

        self.status_changed.emit(
            "Choose the key combinations in the desktop shortcut dialog.", False
        )
        response, results = await self._portal_request(
            "BindShortcuts",
            "oa(sa{sv})sa{sv}",
            [
                self._session_handle,
                shortcuts,
                "",
                {"handle_token": Variant("s", self._token("bind"))},
            ],
        )
        if response == 1:
            next_step = (
                "Use Configure shortcuts to try again."
                if self._version >= 2
                else "Restart Sherpa to try again."
            )
            self.status_changed.emit(
                f"Shortcut setup was cancelled. {next_step}",
                self._version >= 2,
            )
            return
        if response != 0:
            raise PortalError("the desktop could not bind shortcuts")
        self._emit_binding_status(results.get("shortcuts", []))

    def _emit_binding_status(self, shortcuts: Any) -> None:
        message = shortcut_summary(shortcuts)
        if self._version < 2:
            message += " Change them in your desktop’s global shortcut settings."
        self.status_changed.emit(message, self._version >= 2)

    async def _configure_shortcuts(self) -> None:
        if self._configure_in_progress:
            return
        if not self._session_handle or self._version < 2:
            self.status_changed.emit(
                "This desktop portal cannot reopen its shortcut settings.", False
            )
            return
        self._configure_in_progress = True
        self.status_changed.emit("Opening the desktop shortcut settings…", False)
        try:
            await self._call(
                Message(
                    destination=PORTAL_BUS_NAME,
                    path=PORTAL_OBJECT_PATH,
                    interface=SHORTCUTS_INTERFACE,
                    member="ConfigureShortcuts",
                    signature="osa{sv}",
                    body=[self._session_handle, "", {}],
                )
            )
            self.status_changed.emit(
                "Shortcut settings opened. Changes apply immediately.", True
            )
        except BaseException as error:
            self.status_changed.emit(f"Could not open shortcut settings: {error}", True)
        finally:
            self._configure_in_progress = False

    async def _portal_request(
        self, member: str, signature: str, body: list[Any]
    ) -> tuple[int, dict[str, Any]]:
        reply = await self._call(
            Message(
                destination=PORTAL_BUS_NAME,
                path=PORTAL_OBJECT_PATH,
                interface=SHORTCUTS_INTERFACE,
                member=member,
                signature=signature,
                body=body,
            )
        )
        if not reply.body or not isinstance(reply.body[0], str):
            raise PortalError(f"{member} returned no request handle")
        return await self._wait_for_response(reply.body[0])

    async def _wait_for_response(
        self, request_path: str
    ) -> tuple[int, dict[str, Any]]:
        completed = self._responses.pop(request_path, None)
        if completed is not None:
            return completed
        loop = asyncio.get_running_loop()
        future: asyncio.Future[tuple[int, dict[str, Any]]] = loop.create_future()
        self._response_waiters[request_path] = future
        stop_waiter = asyncio.create_task(self._stop_event.wait())  # type: ignore[union-attr]
        try:
            done, _pending = await asyncio.wait(
                (future, stop_waiter),
                timeout=300,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if future in done:
                return future.result()
            if stop_waiter in done:
                raise asyncio.CancelledError
            raise PortalError("the desktop shortcut dialog timed out")
        finally:
            self._response_waiters.pop(request_path, None)
            stop_waiter.cancel()

    async def _call(self, message: Message) -> Message:
        reply = await self._send_message(message)
        if reply.message_type == MessageType.ERROR:
            detail = str(reply.body[0]) if reply.body else reply.error_name
            raise PortalError(detail or "unknown D-Bus error")
        return reply

    async def _send_message(self, message: Message) -> Message:
        """Send a D-Bus message, cancelling promptly when the app exits."""

        if self._bus is None:
            raise PortalError("D-Bus is not connected")
        call = asyncio.ensure_future(self._bus.call(message))
        if self._stop_event is None:
            return await asyncio.wait_for(call, timeout=15)

        stop_waiter = asyncio.create_task(self._stop_event.wait())
        try:
            done, _pending = await asyncio.wait(
                (call, stop_waiter),
                timeout=15,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if call in done:
                return call.result()
            call.cancel()
            try:
                await call
            except asyncio.CancelledError:
                pass
            if stop_waiter in done:
                raise asyncio.CancelledError
            raise PortalError("the desktop portal did not respond")
        finally:
            stop_waiter.cancel()

    def _handle_message(self, message: Message) -> bool:
        if message.message_type != MessageType.SIGNAL:
            return False
        if (
            message.interface == REQUEST_INTERFACE
            and message.member == "Response"
            and len(message.body) >= 2
        ):
            result = (int(message.body[0]), dict(message.body[1]))
            waiter = self._response_waiters.get(message.path)
            if waiter is not None and not waiter.done():
                waiter.set_result(result)
            else:
                self._responses[message.path] = result
            return True
        if message.interface == SHORTCUTS_INTERFACE and len(message.body) >= 2:
            session = str(message.body[0])
            if session != self._session_handle:
                return False
            if message.member == "Activated":
                action = str(message.body[1])
                if action in {item.action for item in SHORTCUT_DEFINITIONS}:
                    self.shortcut_activated.emit(action)
                return True
            if message.member == "ShortcutsChanged":
                self._emit_binding_status(message.body[1])
                return True
        if (
            message.interface == SESSION_INTERFACE
            and message.member == "Closed"
            and message.path == self._session_handle
        ):
            self._session_handle = ""
            self.status_changed.emit("The desktop closed the shortcut session.", False)
            if self._stop_event is not None:
                self._stop_event.set()
            return True
        return False

    async def _close_session(self) -> None:
        if self._bus is None or not self._session_handle:
            return
        try:
            await self._call(
                Message(
                    destination=PORTAL_BUS_NAME,
                    path=self._session_handle,
                    interface=SESSION_INTERFACE,
                    member="Close",
                )
            )
        except BaseException:
            pass
        self._session_handle = ""

    @staticmethod
    def _token(prefix: str) -> str:
        return f"sherpa_{prefix}_{uuid.uuid4().hex}"
