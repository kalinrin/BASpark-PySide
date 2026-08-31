"""
BASpark 应用程序入口点。
"""
import os
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

from utils.helpers import get_resource_path, check_single_instance
from core.window import BASparkWindow


def _get_app_data_dir() -> Path:
    """
    返回与当前操作系统匹配的应用数据目录。

    原实现把所有平台都写到 macOS 风格的 "~/Library/Application Support/BASpark"，
    会在 Windows/Linux 上生成不合常规的目录，这里按平台分别处理。

    Returns:
        Path: 已创建好的应用数据目录。
    """
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:  # Linux 及其它类 Unix 系统
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")

    app_dir = base / "BASpark"
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def _setup_environment() -> None:
    """在创建 QApplication 之前完成所有环境变量配置。

    注意：QtWebEngine 的相关环境变量必须在 QApplication 实例化之前设置，否则不会生效。
    """
    # 关闭 Qt 内部框架的警告与调试日志输出。
    os.environ["QT_LOGGING_RULES"] = "*.debug=false;qt.*.debug=false;*.warning=false"

    # 启用高 DPI 缩放支持（Qt6 下高 DPI 默认开启，此处保留仅为兼容旧行为）
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    # 强制指定 QtWebEngine 的数据/缓存目录，避免污染系统目录或破坏 macOS 签名。
    app_data_dir = _get_app_data_dir()
    os.environ["QTWEBENGINE_USER_DATA_PATH"] = str(app_data_dir)

    # ------------------------------------------------------------------
    # Chromium 渲染引擎参数
    # ------------------------------------------------------------------
    disabled_features: list[str] = [
        # QtWebEngine 会注册系统级媒体键处理。本程序是常驻后台的悬浮层，自己不放
        # 任何媒体，却会把用户的播放/暂停键抢走，因此关掉。
        "HardwareMediaKeyHandling",
    ]

    chromium_flags: list[str] = [
        # 本窗口在设计上永远拿不到焦点（WindowTransparentForInput +
        # WA_ShowWithoutActivating + NoFocus），Chromium 会把这种页面判成
        # "后台/被遮挡"，进而降低渲染进程优先级、节流定时器与 rAF —— 对一个
        # 逐帧重绘的特效层就是直接掉帧。下面三条都是关掉这套降级机制，
        # 三者互不重叠：分别管定时器节流、渲染进程降级、被判遮挡后的降级。
        "--disable-background-timer-throttling",
        "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows",
        # 鼠标移动会持续从 Python 侧向渲染进程推消息，别让 IPC 洪泛保护把它掐掉
        "--disable-ipc-flooding-protection",
        # 只保留 FATAL 级日志
        "--log-level=3",
    ]

    if sys.platform == "win32":
        # Windows 专有的原生窗口遮挡检测。官方文档写得很直接：一旦窗口被判定为
        # 被遮挡，Chromium 就把前台标签当后台处理 —— 停止渲染、节流 JS；而且
        # "被误判为遮挡时内容区会变白"。全屏置顶透明窗正是最容易被误判的形态。
        # 注意它与上面的 --disable-backgrounding-occluded-windows 是两套机制：
        # 这条管"要不要做遮挡判定"，那条管"判定为遮挡后要不要降级"，必须都关。
        disabled_features.append("CalculateNativeWinOcclusion")
    elif sys.platform == "darwin":
        pass

    if disabled_features:
        chromium_flags.append("--disable-features=" + ",".join(disabled_features))

    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(chromium_flags)


def main() -> int:
    """应用主入口。

    Returns:
        int: 进程退出码。
    """
    _setup_environment()

    app = QApplication(sys.argv)

    # IPC 单实例锁机制检查
    # lock_server 必须在整个进程生命周期内保持引用，否则锁会被释放导致单实例失效。
    lock_server = check_single_instance()
    if lock_server is None:
        print("BASpark 正在运行中！")
        return 0

    # 防止无可见窗口时 Qt 主循环意外退出
    app.setQuitOnLastWindowClosed(False)

    # 设置全局图标
    icon_path = get_resource_path("app.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = BASparkWindow()

    # 将窗口与单实例锁挂到 app 上，避免被垃圾回收提前释放。
    app.baspark_window = window
    app.baspark_lock = lock_server

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
