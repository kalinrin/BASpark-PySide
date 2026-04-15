"""
BASpark 应用程序入口点。
"""
import sys
import os
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon

from utils.helpers import get_resource_path, check_single_instance
from core.window import BASparkWindow

def main():
    # 启用高 DPI 缩放支持
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    # Chromium 渲染引擎性能与兼容性参数优化
    sys.argv.extend([
        "--disable-background-timer-throttling",
        "--disable-features=CalculateNativeWinOcclusion",
        "--disable-ipc-flooding-protection",
        "--log-level=3",
        "--disable-logging"
    ])

    if sys.platform == 'darwin':
        sys.argv.extend(["--disable-renderer-backgrounding", "--disable-mac-overlays"])
    else:
        sys.argv.extend(["--disable-gpu-compositing", "--ignore-gpu-blocklist"])

    app = QApplication(sys.argv)

    # IPC 单实例锁机制检查
    lock_server = check_single_instance()
    if not lock_server:
        print("BASpark 正在运行中！")
        sys.exit(0)

    # 防止无可见窗口时 Qt 主循环意外退出
    app.setQuitOnLastWindowClosed(False)

    # 设置全局图标
    icon_path = get_resource_path("app.ico")
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    window = BASparkWindow()
    sys.exit(app.exec())

if __name__ == '__main__':
    main()