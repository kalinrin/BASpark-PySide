"""
主窗口与 WebEngine 渲染模块。
"""
import sys
import ctypes
from PySide6.QtCore import Qt, QUrl, QTimer
from PySide6.QtWidgets import QMainWindow, QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtGui import QCursor

from utils.helpers import get_resource_path
from core.mouse_hook import MouseTracker
from core.tray import AppTray


class BASparkWindow(QMainWindow):
    """
    负责透明悬浮窗的渲染、层级管理以及与 Web 前端的通信。
    """

    def __init__(self):
        super().__init__()

        # 初始化坐标缓存
        self._last_sent_pos = (-1, -1)

        self._init_window_attributes()
        self._init_browser()

        # 初始化鼠标追踪
        self.tracker = MouseTracker()
        self.tracker.signals.clicked.connect(self._trigger_boom)
        self.tracker.signals.moved.connect(self._trigger_move)
        self.tracker.signals.released.connect(self._trigger_up)
        self.tracker.start()

        # 初始化托盘
        self.tray = AppTray(self)
        self.tray.show()

        # 初始化 Win32 窗口置顶保活机制
        if sys.platform == 'win32':
            self.topmost_timer = QTimer(self)
            self.topmost_timer.timeout.connect(self._keep_on_top)
            self.topmost_timer.start(1000)

        self._adapt_screen()

    def _init_window_attributes(self):
        """初始化窗口核心渲染属性与层级策略。"""
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.ToolTip |
            Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        if sys.platform == 'darwin':
            self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow, True)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

    def _init_browser(self):
        """初始化 Chromium WebEngine 容器。"""
        self.browser = QWebEngineView(self)
        self.setCentralWidget(self.browser)
        self.browser.page().setBackgroundColor(Qt.GlobalColor.transparent)

        html_path = get_resource_path("web/index.html")
        if html_path.exists():
            self.browser.setUrl(QUrl.fromLocalFile(str(html_path)))

        self.browser.loadFinished.connect(self._on_load_finished)

    def _adapt_screen(self):
        """跨平台屏幕自适应逻辑。"""
        if sys.platform == 'darwin':
            self.setContentsMargins(0, 0, 0, 0)
            self.setGeometry(QApplication.primaryScreen().geometry())
            self.show()
        else:
            self.showFullScreen()

    def _keep_on_top(self):
        """Win32 置顶保活策略。"""
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0013)  # HWND_NOTOPMOST
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)  # HWND_TOPMOST
        except Exception:
            pass

    def _on_load_finished(self, ok: bool):
        """前端页面加载完毕后的平台特有补丁。"""
        if not ok: return
        if sys.platform == 'win32':
            QTimer.singleShot(100, self._apply_windows_transparency)
        elif sys.platform == 'darwin':
            QTimer.singleShot(100, self._apply_macos_transparency)

    def _apply_windows_transparency(self):
        """向当前窗口注入底层事件穿透扩展样式 (Win32)。"""
        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        ex_style = user32.GetWindowLongW(hwnd, -20)
        user32.SetWindowLongW(hwnd, -20, ex_style | 0x00000020)

    def _apply_macos_transparency(self):
        """忽略 macOS 端的鼠标事件注入。"""
        try:
            from AppKit import NSApp
            for window in NSApp.windows():
                window.setIgnoresMouseEvents_(True)
        except ImportError:
            pass

    def force_refresh_window(self):
        """手动重置窗口 Z 序：隐藏后延迟重建显示状态。"""
        self.hide()
        QTimer.singleShot(100, self._reshow_and_topmost)

    def _reshow_and_topmost(self):
        if sys.platform == 'win32':
            self.showFullScreen()
            self._apply_windows_transparency()
        else:
            self.show()

    def change_theme_color(self, rgb_str: str):
        """动态更新前端特效配色。"""
        self.browser.page().runJavaScript(f"if(window.updateColor)window.updateColor('{rgb_str}');")

    def _get_logic_pos(self):
        """坐标映射：计算相对于应用窗口的逻辑坐标。"""
        gp = QCursor.pos()
        wp = self.pos()
        lx, ly = gp.x() - wp.x(), gp.y() - wp.y()
        if sys.platform == 'darwin':
            ly -= 25  # macOS 顶部菜单栏高度补偿
        return lx, ly

    def _trigger_boom(self):
        lx, ly = self._get_logic_pos()
        self.browser.page().runJavaScript(f"if(window.externalBoom)window.externalBoom({lx},{ly});")

    def _trigger_move(self):
        """处理鼠标移动信号，并向 Web 端发送最新逻辑坐标"""
        lx, ly = self._get_logic_pos()
        # 性能优化，如果坐标没有发生物理级变化，直接拦截，不发送跨进程 IPC 指令
        if self._last_sent_pos == (lx, ly):
            return
        # 更新缓存坐标
        self._last_sent_pos = (lx, ly)
        # 向 Chromium 发送执行指令
        self.browser.page().runJavaScript(f"if(window.externalMove)window.externalMove({lx},{ly});")

    def _trigger_up(self):
        self.browser.page().runJavaScript("if(window.externalUp)window.externalUp();")

    def closeEvent(self, event):
        """截获关闭事件，释放底层资源。"""
        self.tracker.stop()
        super().closeEvent(event)