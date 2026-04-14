"""
全局鼠标事件监听模块。
"""
import time
from PySide6.QtCore import QObject, Signal
from pynput import mouse

class MouseSignals(QObject):
    """
    跨线程信号源：用于将 pynput 后台线程的事件安全转发至 PySide 主线程。
    """
    clicked = Signal()
    moved = Signal()
    released = Signal()

class MouseTracker:
    """
    鼠标轨迹与点击事件追踪器。
    """
    def __init__(self, move_throttle_ms: int = 24):
        """
        初始化鼠标追踪器。

        Args:
            move_throttle_ms (int): 鼠标移动事件的节流阈值（毫秒），24ms 约等于 41Hz。
        """
        self.signals = MouseSignals()
        self.last_move_time = 0
        self.move_throttle_ms = move_throttle_ms
        self.listener = None

    def start(self):
        """启动全局鼠标监听后台线程。"""
        self.listener = mouse.Listener(
            on_click=self._on_global_click,
            on_move=self._on_global_move
        )
        self.listener.start()

    def stop(self):
        """停止全局鼠标监听。"""
        if self.listener:
            self.listener.stop()

    def _on_global_click(self, x: float, y: float, button: mouse.Button, pressed: bool):
        """处理鼠标点击事件。"""
        if button == mouse.Button.left:
            if pressed:
                self.signals.clicked.emit()
            else:
                self.signals.released.emit()

    def _on_global_move(self, x: float, y: float):
        """处理鼠标移动事件（带节流控制）。"""
        curr = time.time() * 1000
        if curr - self.last_move_time > self.move_throttle_ms:
            self.last_move_time = curr
            self.signals.moved.emit()