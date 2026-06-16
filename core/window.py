"""
主窗口与 WebEngine 渲染模块。

实现透明、全屏、鼠标穿透且始终置顶的悬浮窗：
内部以 QWebEngineView 渲染前端页面，并把全局鼠标事件转发给前端。
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
    """透明悬浮窗：负责渲染、窗口层级管理以及与前端的通信。"""

    def __init__(self):
        super().__init__()

        # 上一次发送给前端的坐标，用于去重
        self._last_sent_pos = (-1, -1)
        self.settings_window = None

        self._init_window_attributes()
        self._init_browser()

        # 鼠标追踪：点击 / 移动 / 释放分别驱动前端特效
        self.tracker = MouseTracker()
        self.tracker.signals.clicked.connect(self._trigger_boom)
        self.tracker.signals.moved.connect(self._trigger_move)
        self.tracker.signals.released.connect(self._trigger_up)
        self.tracker.start()

        # 系统托盘
        self.tray = AppTray(self)
        self.tray.show()

        # Windows 下定时重新置顶，避免被其它窗口覆盖
        if sys.platform == 'win32':
            self.topmost_timer = QTimer(self)
            self.topmost_timer.timeout.connect(self._keep_on_top)
            self.topmost_timer.start(1000)

        self._adapt_screen()

    def _init_window_attributes(self):
        """设置无边框、置顶、透明背景及鼠标穿透等窗口属性。"""
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
        """创建 WebEngine 视图并加载前端页面。"""
        self.browser = QWebEngineView(self)
        self.setCentralWidget(self.browser)
        self.browser.page().setBackgroundColor(Qt.GlobalColor.transparent)

        html_path = get_resource_path("web/index.html")
        if html_path.exists():
            self.browser.setUrl(QUrl.fromLocalFile(str(html_path)))

        self.browser.loadFinished.connect(self._on_load_finished)

    def _adapt_screen(self):
        """按平台铺满主屏显示。"""
        if sys.platform == 'darwin':
            self.setContentsMargins(0, 0, 0, 0)
            self.setGeometry(QApplication.primaryScreen().geometry())
            self.show()
        else:
            self.showFullScreen()

    def _keep_on_top(self):
        """Windows 下强制刷新置顶（先取消再置顶以提升层级优先级）。"""
        try:
            hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0013)  # HWND_NOTOPMOST
            user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)  # HWND_TOPMOST
        except Exception:
            pass

    def _on_load_finished(self, ok: bool):
        """页面加载完成后，应用各平台的鼠标穿透补丁。"""
        if not ok: return
        if sys.platform == 'win32':
            QTimer.singleShot(100, self._apply_windows_transparency)
        elif sys.platform == 'darwin':
            QTimer.singleShot(100, self._apply_macos_transparency)

    def _apply_windows_transparency(self):
        """为窗口追加 WS_EX_TRANSPARENT 扩展样式，实现鼠标点击穿透 (Win32)。"""
        hwnd = int(self.winId())
        user32 = ctypes.windll.user32
        ex_style = user32.GetWindowLongW(hwnd, -20)              # GWL_EXSTYLE
        user32.SetWindowLongW(hwnd, -20, ex_style | 0x00000020)  # WS_EX_TRANSPARENT

    def _apply_macos_transparency(self):
        """让 macOS 原生窗口忽略鼠标事件，实现点击穿透。"""
        try:
            from AppKit import NSApp
            for window in NSApp.windows():
                window.setIgnoresMouseEvents_(True)
        except ImportError:
            pass

    def force_refresh_window(self):
        """重置窗口层级：先隐藏，延迟后重新显示并置顶。"""
        self.hide()
        QTimer.singleShot(100, self._reshow_and_topmost)

    def _reshow_and_topmost(self):
        """重新显示窗口并恢复置顶状态。"""
        if sys.platform == 'win32':
            self.showFullScreen()
            self._apply_windows_transparency()
        else:
            self.show()

    def change_theme_color(self, rgb_str: str):
        """通知前端切换特效配色。

        Args:
            rgb_str (str): 形如 "76,167,255" 的 RGB 字符串。
        """
        self.browser.page().runJavaScript(f"if(window.updateColor)window.updateColor('{rgb_str}');")

    def _get_logic_pos(self):
        """将光标全局坐标换算为浏览器视图内的百分比坐标 (0.0 ~ 1.0)。"""
        # mapFromGlobal 会自动处理窗口偏移（如 macOS 菜单栏、程序坞）
        local_pos = self.browser.mapFromGlobal(QCursor.pos())

        bw = self.browser.width()
        bh = self.browser.height()

        # 视图尺寸未就绪时返回中心点，避免除零
        if bw == 0 or bh == 0:
            return 0.5, 0.5

        # 用百分比表示，前端按视口尺寸还原为像素坐标
        percent_x = local_pos.x() / bw
        percent_y = local_pos.y() / bh

        return percent_x, percent_y

    def _trigger_boom(self):
        """左键按下：在光标处触发前端点击特效。"""
        lx, ly = self._get_logic_pos()
        self.browser.page().runJavaScript(f"if(window.externalBoom)window.externalBoom({lx},{ly});")

    def _trigger_move(self):
        """鼠标移动：向前端发送最新坐标。"""
        lx, ly = self._get_logic_pos()
        # 坐标未变化时跳过，减少跨进程调用
        if self._last_sent_pos == (lx, ly):
            return
        self._last_sent_pos = (lx, ly)
        self.browser.page().runJavaScript(f"if(window.externalMove)window.externalMove({lx},{ly});")

    def _trigger_up(self):
        """左键释放：通知前端结束当前交互。"""
        self.browser.page().runJavaScript("if(window.externalUp)window.externalUp();")

    def show_settings_window(self):
        """打开设置面板，确保单例并显示在最前面，且默认打开‘关于’页面。"""
        from core.settings_window import SettingsWindow
        
        # 检查 settings_window 是否由于被关闭而销毁
        if self.settings_window is not None:
            try:
                # 若窗口已被 C++ 销毁，调用任何方法都会抛出 RuntimeError
                self.settings_window.parent()
            except RuntimeError:
                self.settings_window = None

        if self.settings_window is None:
            self.settings_window = SettingsWindow(self)

        # 每次打开都重新读取系统实际状态，避免显示未应用的旧开关状态
        self.settings_window._load_settings()
        self.settings_window.show_about_page()
        self.settings_window.show()
        self.settings_window.raise_()
        self.settings_window.activateWindow()

    def closeEvent(self, event):
        """窗口关闭时停止鼠标监听，释放资源。"""
        self.tracker.stop()
        super().closeEvent(event)
