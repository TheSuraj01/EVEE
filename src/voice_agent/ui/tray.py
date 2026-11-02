"""The system tray: the app's always-present UI (tray first, main window second).

A thin view. Every menu action calls the service; state is polled from it, so the voice
thread never touches a widget.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import QObject, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from voice_agent.ui.status import describe
from voice_agent.wakeword.activation_controller import State

POLL_MS = 200


class TrayService(Protocol):
    @property
    def state(self) -> State: ...

    @property
    def muted(self) -> bool: ...

    @property
    def running(self) -> bool: ...

    @property
    def loading(self) -> bool: ...

    @property
    def asleep(self) -> bool: ...

    def mute(self) -> None: ...

    def unmute(self) -> None: ...

    def emergency_stop(self) -> None: ...

    def tap(self) -> None: ...


def dot_icon(color: str, size: int = 32) -> QIcon:
    """A filled circle: no image assets to ship, and the colour is the state."""
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(QColor(color))
    painter.drawEllipse(2, 2, size - 4, size - 4)
    painter.end()
    return QIcon(pixmap)


class Tray(QObject):
    def __init__(
        self,
        service: TrayService,
        *,
        on_settings: Callable[[], None],
        on_logs: Callable[[], None],
        on_quit: Callable[[], None],
        on_clear_history: Callable[[], None] = lambda: None,
        on_copy_diagnostics: Callable[[], None] = lambda: None,
    ) -> None:
        super().__init__()
        self._service = service
        self._color = ""
        self._status = ""
        self.menu = QMenu()
        self.toggle_action = self.menu.addAction("Mute", self._toggle)
        self.stop_action = self.menu.addAction("Emergency stop", self._emergency_stop)
        self.menu.addSeparator()
        self.menu.addAction("Settings...", on_settings)
        self.menu.addAction("Open logs", on_logs)
        self.menu.addAction("Clear command history", on_clear_history)
        self.menu.addAction("Copy diagnostics", on_copy_diagnostics)
        self.menu.addSeparator()
        self.menu.addAction("Quit", on_quit)

        self.icon = QSystemTrayIcon()
        self.icon.setContextMenu(self.menu)
        self.icon.activated.connect(self._on_activated)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(POLL_MS)
        self.refresh()
        self.icon.show()

    def refresh(self) -> None:
        service = self._service
        label, color = describe(
            service.state, service.muted, service.running, service.loading, service.asleep
        )
        self.toggle_action.setText("Listen" if self._service.muted else "Mute")
        if color != self._color:
            self.icon.setIcon(dot_icon(color))
            self._color = color
        status = f"\n{self._status}" if self._status else ""
        self.icon.setToolTip(f"Voice PC Agent — {label}{status}")

    def show_stop_key(self, spec: str | None) -> None:
        """Name the emergency-stop key that really works, where it can't be missed: a
        notification about a fallback can be hidden by Do Not Disturb, the menu can't."""
        self.stop_action.setText(f"Emergency stop ({spec or 'no hotkey'})")

    def set_status(self, message: str) -> None:
        """Latest service message (loading, mic lost...), shown on hover rather than as a
        stream of pop-ups."""
        self._status = message
        self.refresh()

    def toast(self, message: str) -> None:
        self.icon.showMessage("Voice PC Agent", message, QSystemTrayIcon.MessageIcon.Information)

    def _toggle(self) -> None:
        if self._service.muted:
            self._service.unmute()
        else:
            self._service.mute()
        self.refresh()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """Left-click on the dot does what a click on the indicator does; right-click keeps
        opening the menu."""
        if reason is QSystemTrayIcon.ActivationReason.Trigger:
            self._service.tap()
            self.refresh()

    def _emergency_stop(self) -> None:
        self._service.emergency_stop()
        self.refresh()
