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
    # 关闭 Qt 内部框架的警告与调试日志输出
    os.environ["QT_LOGGING_RULES"] = "*.debug=false;qt.*.debug=false;*.warning=false"

    # 启用高 DPI 缩放支持（Qt6 下高 DPI 默认开启，此处保留仅为兼容旧行为）
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    # 强制指定 QtWebEngine 的数据/缓存目录，避免污染系统目录或破坏 macOS 签名。
    # 该环境变量按整体读取，路径中的空格不会被拆分，可放心使用包含空格的路径。
    app_data_dir = _get_app_data_dir()
    os.environ["QTWEBENGINE_USER_DATA_PATH"] = str(app_data_dir)

    # Chromium 渲染引擎性能与兼容性参数。
    # 这些参数必须通过 QTWEBENGINE_CHROMIUM_FLAGS 传递，而不能塞进 sys.argv，
    # 否则 Qt 会把它们当作未知命令行选项并打印警告，同时也无法真正传给 Chromium。
    chromium_flags = [
        "--disable-background-timer-throttling",
        "--disable-features=CalculateNativeWinOcclusion",
        "--disable-ipc-flooding-protection",
        "--log-level=3",
        "--disable-logging",
    ]

    if sys.platform == "darwin":
        chromium_flags += ["--disable-renderer-backgrounding", "--disable-mac-overlays"]
    else:
        chromium_flags += ["--disable-gpu-compositing", "--ignore-gpu-blocklist"]

    # 这里不再单独设置 --disk-cache-dir：
    #   1) QTWEBENGINE_USER_DATA_PATH 已经决定了缓存的存储位置；
    #   2) QTWEBENGINE_CHROMIUM_FLAGS 以空格分隔，含空格的路径
    #      （如 macOS 的 "Application Support"）会被截断而导致参数失效。
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
