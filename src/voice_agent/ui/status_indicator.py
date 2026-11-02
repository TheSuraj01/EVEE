"""The always-visible indicator: a small pill that shows what the agent is doing and is
its main control. Click it to give a command (or to listen again when muted), right-click
for the menu, drag it out of the way; it remembers where it was left.

It must never take focus: commands act on the focused window, so a click must leave focus
where it was. It starts bottom-right, away from the minimise/maximise/close buttons of
maximised windows. It polls the service like the tray does.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QMenu, QWidget

from voice_agent.ui.status import describe
from voice_agent.ui.tray import POLL_MS, TrayService

MARGIN = 12
#: A press that moves further than this is a drag, not a click.
DRAG_PX = 6
#: Opaque, so contrast holds over any app: the words are near-white (WCAG AA), and the
#: state colour goes on the dot, where 3:1 is enough.
PILL, TEXT = "#141414", "#f5f5f5"
HINT = "Click: start or stop listening (or unmute)\nRight-click: menu · Drag: move"


class StatusIndicator(QWidget):
    def __init__(
        self,
        service: TrayService,
        menu: QMenu | None = None,
        on_moved: Callable[[tuple[int, int]], None] | None = None,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool  # no taskbar button
            | Qt.WindowType.WindowDoesNotAcceptFocus,  # clicks never take focus from your app
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(HINT)
        self._service = service
        self._menu = menu
        self._on_moved = on_moved or (lambda _pos: None)
        self._press: QPoint | None = None
        self._origin = QPoint()
        self._dragging = False
        self.label = QLabel(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.label)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(POLL_MS)
        self.refresh()

    def refresh(self) -> None:
        service = self._service
        text, color = describe(
            service.state, service.muted, service.running, service.loading, service.asleep
        )
        self.label.setText(f'<span style="color: {color}">●</span> {text}')
        self.label.setStyleSheet(
            f"color: {TEXT}; background: {PILL}; border-radius: 10px;"
            "padding: 3px 10px; font-weight: 600;"
        )
        self.adjustSize()

    def place(self, saved: tuple[int, int] | None = None) -> None:
        """Where the user left it, if that is still on a screen; else bottom-right of the
        primary screen's usable area."""
        if saved is not None and QApplication.screenAt(QPoint(*saved)) is not None:
            self.move(*saved)
            return
        screen = QApplication.primaryScreen()
        if screen is not None:
            area = screen.availableGeometry()
            self.move(area.right() - self.width() - MARGIN, area.bottom() - self.height() - MARGIN)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        at = event.globalPosition().toPoint()
        if event.button() is Qt.MouseButton.RightButton:
            if self._menu is not None:
                self._menu.popup(at)
        elif event.button() is Qt.MouseButton.LeftButton:
            self._press, self._origin, self._dragging = at, self.pos(), False

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if self._press is None or not event.buttons() & Qt.MouseButton.LeftButton:
            return
        offset = event.globalPosition().toPoint() - self._press
        if self._dragging or offset.manhattanLength() > DRAG_PX:
            self._dragging = True
            self.move(self._origin + offset)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802 - Qt override
        if event.button() is not Qt.MouseButton.LeftButton or self._press is None:
            return
        self._press = None
        if self._dragging:
            self._on_moved((self.x(), self.y()))
        else:
            self._service.tap()
            self.refresh()
