"""
主窗口与 WebEngine 渲染模块。

实现透明、全屏、鼠标穿透且始终置顶的悬浮窗：
内部以 QWebEngineView 渲染前端页面，并把全局鼠标事件转发给前端。
"""
import sys
import ctypes
from PySide6.QtCore import Qt, QUrl, QTimer, QObject, QFile, QIODevice, Signal, Slot
from PySide6.QtWidgets import QMainWindow, QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineScript
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtGui import QCursor

from utils.helpers import get_resource_path
from core.mouse_hook import MouseTracker
from core.tray import AppTray


# 前端连接用的胶水脚本。
# 前端 overlay.js 的对外接口（window.externalMove / externalBoom / externalUp /
# updateColor）保持原样不动，这段脚本只负责把 QWebChannel 的信号转接到它们上面，
# 因此不需要改动 web/ 下的任何文件。
_BRIDGE_CONNECT_JS = """
(function () {
    'use strict';
    // 没有 transport 说明 setWebChannel 没生效（例如脚本先于通道建立就跑了）
    if (typeof qt === 'undefined' || !qt.webChannelTransport) { return; }
    if (window.__basparkBridge) { return; }
    new QWebChannel(qt.webChannelTransport, function (channel) {
        var b = channel.objects.bridge;
        if (!b) { return; }
        window.__basparkBridge = b;
        // 每次调用都重新判断函数是否存在：overlay.js 可能在资源就绪后才挂上这些接口，
        // 语义与原先 runJavaScript 里的 if(window.externalMove) 完全一致。
        b.moved.connect(function (x, y) {
            if (typeof window.externalMove === 'function') { window.externalMove(x, y); }
        });
        b.clicked.connect(function (x, y) {
            if (typeof window.externalBoom === 'function') { window.externalBoom(x, y); }
        });
        b.released.connect(function () {
            if (typeof window.externalUp === 'function') { window.externalUp(); }
        });
        b.colorChanged.connect(function (rgb) {
            if (typeof window.updateColor === 'function') { window.updateColor(rgb); }
        });
        // 握手：告诉 Python 侧"从现在起 emit 才有人接"
        b.notifyReady();
    });
})();
"""


class FrontendBridge(QObject):
    """Python -> 前端的数据通道（经 QWebChannel 暴露给 JS）。

    数据是单向的：Python 发信号，JS 接收。唯一的反向调用是 notifyReady() 握手。

    这样做替代了原先"每个鼠标事件都拼一条 JS 源码字符串交给 runJavaScript"的做法 ——
    那种方式每次都要走一趟跨进程 IPC + 一次 V8 解析编译，而且坐标是拼进源码文本的。
    改成信号后传的是结构化参数，省掉了字符串拼接与每次的脚本编译。
    """

    # 信号名会原样出现在 JS 侧（b.moved / b.clicked / ...）
    moved = Signal(float, float)
    clicked = Signal(float, float)
    released = Signal()
    colorChanged = Signal(str)

    # 仅供 Python 内部使用的握手通知（同时也会被暴露给 JS，但前端不会用到）
    ready = Signal()

    @Slot()
    def notifyReady(self):
        """由前端胶水脚本在通道建立完成后调用。"""
        self.ready.emit()


class BASparkWindow(QMainWindow):
    """透明悬浮窗：负责渲染、窗口层级管理以及与前端的通信。"""

    def __init__(self):
        super().__init__()

        # 上一次发送给前端的坐标，用于去重
        self._last_sent_pos = (-1, -1)
        self.settings_window = None

        # QWebChannel 相关状态
        self.bridge = None            # FrontendBridge 实例（通道可用时才创建）
        self.channel = None           # QWebChannel 实例
        self._channel_ready = False   # 前端胶水脚本是否已完成握手
        self._legacy_js = False       # 通道建立失败时回退到 runJavaScript
        self._pending_color = None    # 通道就绪前用户改的配色，就绪后补发

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

        # ★ 必须在 setUrl 之前建立通道 ★
        # qt.webChannelTransport 是 WebEngine 在页面加载时注入的，只有在加载开始前
        # 就设好 web channel，本次加载的页面里才会有 transport。
        self._legacy_js = not self._init_bridge()

        html_path = get_resource_path("web/index.html")
        if html_path.exists():
            self.browser.setUrl(QUrl.fromLocalFile(str(html_path)))

        # 页面重新加载时 JS 上下文会重建，旧的握手随之失效，必须把 ready 状态清掉，
        # 否则会在新页面还没连上通道的空窗期里对着虚空 emit。
        self.browser.loadStarted.connect(self._on_load_started)
        self.browser.loadFinished.connect(self._on_load_finished)

    def _init_bridge(self) -> bool:
        """建立 QWebChannel 并向前端注入连接脚本。

        qwebchannel.js 以 Qt 资源形式随 WebChannel 模块分发（:/qtwebchannel/qwebchannel.js），
        不需要往 web/ 里复制文件，也不会给打包产物增加体积。

        Returns:
            bool: 成功返回 True；失败（拿不到该 Qt 资源）返回 False，
                  调用方将回退到旧的 runJavaScript 路径，功能不丢。
        """
        f = QFile(":/qtwebchannel/qwebchannel.js")
        if not f.open(QIODevice.OpenModeFlag.ReadOnly):
            print("[BASpark] 无法读取 :/qtwebchannel/qwebchannel.js，"
                  "事件推送回退到 runJavaScript", file=sys.stderr)
            return False
        qwebchannel_src = bytes(f.readAll()).decode("utf-8")
        f.close()

        self.bridge = FrontendBridge(self)
        self.bridge.ready.connect(self._on_bridge_ready)

        self.channel = QWebChannel(self)
        self.channel.registerObject("bridge", self.bridge)
        self.browser.page().setWebChannel(self.channel)

        # 注入到主世界（MainWorld）：胶水脚本要同时够到 qt.webChannelTransport、
        # QWebChannel 构造器，以及页面自己的 window.externalMove 等接口。
        # 注入点选 DocumentCreation：越早跑，首帧事件丢失的空窗期越短。
        # 注意：当前 PySide6 版本的 QWebEngineScript 构造函数不接受 parent 位置参数
        # （只支持拷贝构造或全关键字参数），这里必须无参构造。
        # 脚本随后 insert 进 page 的 scripts() 集合，由该集合管理生命周期，无需 parent。
        script = QWebEngineScript()
        script.setName("baspark_bridge_connector")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(qwebchannel_src + "\n;\n" + _BRIDGE_CONNECT_JS)
        self.browser.page().scripts().insert(script)
        return True

    def _on_load_started(self):
        """页面开始（重新）加载：JS 上下文即将重建，握手状态作废。"""
        self._channel_ready = False

    def _on_bridge_ready(self):
        """前端胶水脚本握手完成：此后 emit 的信号才有人接收。"""
        self._channel_ready = True
        # 通道就绪前用户改过配色的话，此刻补发，避免"改了没生效"
        if self._pending_color is not None:
            rgb = self._pending_color
            self._pending_color = None
            self.bridge.colorChanged.emit(rgb)

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
        if self._legacy_js:
            self.browser.page().runJavaScript(
                f"if(window.updateColor)window.updateColor('{rgb_str}');")
            return
        if self._channel_ready:
            self.bridge.colorChanged.emit(rgb_str)
        else:
            # 通道还没握手（典型场景：首启后立刻进设置改配色）。
            # 先记下来，_on_bridge_ready 里补发，否则这次修改会被静默吞掉。
            self._pending_color = rgb_str

    def _get_logic_pos(self):
        """将光标全局坐标换算为浏览器视图内的百分比坐标 (0.0 ~ 1.0)。"""
        # mapFromGlobal 会自动处理窗口偏移（如 macOS 菜单栏、程序坞）
        local_pos = self.browser.mapFromGlobal(QCursor.pos())

        bw = self.browser.width()
        bh = self.browser.height()

        # 视图尺寸未就绪时返回中心点，避免除零
        if bw == 0 or bh == 0:
            return 0.5, 0.5

        # 用百分比表示，前端按视口尺寸还原为像素坐标。
        # 保留 5 位小数（2560px 宽的屏上约 0.026px 精度）：既缩小传输量，
        # 也让 _last_sent_pos 的去重真正可能命中 —— 原始浮点几乎永不相等，
        # 旧实现里这个去重基本是摆设。
        percent_x = round(local_pos.x() / bw, 5)
        percent_y = round(local_pos.y() / bh, 5)

        return percent_x, percent_y

    def _trigger_boom(self):
        """左键按下：在光标处触发前端点击特效。"""
        lx, ly = self._get_logic_pos()
        if self._channel_ready:
            self.bridge.clicked.emit(lx, ly)
        else:
            # 通道未就绪（建立失败，或首启握手还没完成）时的回退路径
            self.browser.page().runJavaScript(
                f"if(window.externalBoom)window.externalBoom({lx},{ly});")

    def _trigger_move(self):
        """鼠标移动：向前端发送最新坐标。"""
        lx, ly = self._get_logic_pos()
        # 坐标未变化时跳过，减少跨进程调用
        if self._last_sent_pos == (lx, ly):
            return
        self._last_sent_pos = (lx, ly)
        if self._channel_ready:
            self.bridge.moved.emit(lx, ly)
        else:
            self.browser.page().runJavaScript(
                f"if(window.externalMove)window.externalMove({lx},{ly});")

    def _trigger_up(self):
        """左键释放：通知前端结束当前交互。"""
        if self._channel_ready:
            self.bridge.released.emit()
        else:
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
