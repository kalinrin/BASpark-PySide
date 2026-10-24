"""
全局鼠标事件监听模块。

基于 pynput 在后台线程捕获全局鼠标的点击与移动事件，
再通过 Qt 信号转发到主线程，供窗口逻辑安全调用。
"""
import time
from PySide6.QtCore import QObject, Signal
from pynput import mouse


class MouseSignals(QObject):
    """跨线程信号源：将后台线程的鼠标事件安全转发至 Qt 主线程。"""

    clicked = Signal()   # 左键按下
    moved = Signal()     # 鼠标移动（已节流）
    released = Signal()  # 左键释放


class MouseTracker:
    """鼠标移动与点击事件追踪器。"""

    def __init__(self, move_throttle_ms: int = 24):
        """
        Args:
            move_throttle_ms (int): 移动事件的节流间隔（毫秒），24ms 约合 41Hz。
        """
        self.signals = MouseSignals()
        self.last_move_time = 0
        self.move_throttle_ms = move_throttle_ms
        self.listener = None

    def start(self):
        """启动后台监听线程。"""
        self.listener = mouse.Listener(
            on_click=self._on_global_click,
            on_move=self._on_global_move
        )
        self.listener.start()

    def stop(self):
        """停止后台监听线程。"""
        if self.listener:
            self.listener.stop()

    def _on_global_click(self, x: float, y: float, button: mouse.Button, pressed: bool):
        """处理点击事件，仅转发左键的按下与释放。"""
        if button == mouse.Button.left:
            if pressed:
                self.signals.clicked.emit()
            else:
                self.signals.released.emit()

    def _on_global_move(self, x: float, y: float):
        """处理移动事件，按节流间隔转发，避免过于频繁。"""
        curr = time.time() * 1000
        if curr - self.last_move_time > self.move_throttle_ms:
            self.last_move_time = curr
            self.signals.moved.emit()
