"""
系统托盘与右键菜单模块。
"""
from PySide6.QtWidgets import QSystemTrayIcon, QMenu, QStyle, QApplication
from PySide6.QtGui import QAction, QIcon, QActionGroup
from utils.helpers import get_resource_path


class AppTray(QSystemTrayIcon):
    """应用程序的系统托盘图标及右键菜单。"""

    def __init__(self, parent_window):
        """
        Args:
            parent_window (QMainWindow): 绑定的主窗口实例，用于菜单回调。
        """
        super().__init__(parent_window)
        self.window = parent_window
        self._init_ui()

    def _init_ui(self):
        """构建托盘图标与菜单项。"""
        icon_path = get_resource_path("app.ico")
        icon = QIcon(str(icon_path))
        if icon.isNull():  # 资源缺失时回退到系统内置图标
            icon = self.window.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)

        self.setIcon(icon)
        self.setToolTip("BASpark")

        tray_menu = QMenu()

        # --- 特效配色子菜单 ---
        color_menu = QMenu("特效配色", self.window)

        self.action_arona = QAction("阿洛娜 (默认蓝)", self.window)
        self.action_arona.setCheckable(True)
        self.action_arona.setChecked(True)
        self.action_arona.triggered.connect(lambda: self.window.change_theme_color('76,167,255'))

        self.action_plana = QAction("普拉娜 (普拉娜粉)", self.window)
        self.action_plana.setCheckable(True)
        self.action_plana.setChecked(False)
        self.action_plana.triggered.connect(lambda: self.window.change_theme_color('255,76,166'))

        # 配色项互斥，保证同时只有一个被选中
        self.color_group = QActionGroup(self.window)
        self.color_group.addAction(self.action_arona)
        self.color_group.addAction(self.action_plana)
        self.color_group.setExclusive(True)

        color_menu.addAction(self.action_arona)
        color_menu.addAction(self.action_plana)
        tray_menu.addMenu(color_menu)
        tray_menu.addSeparator()

        # --- 其他操作 ---
        reset_action = QAction("重置窗口层级", self.window)
        reset_action.triggered.connect(self.window.force_refresh_window)
        tray_menu.addAction(reset_action)
        tray_menu.addSeparator()

        exit_action = QAction("退出", self.window)
        exit_action.triggered.connect(self._quit_app)
        tray_menu.addAction(exit_action)

        self.setContextMenu(tray_menu)

    def _quit_app(self):
        """关闭主窗口并退出应用。"""
        self.window.close()
        QApplication.quit()
