"""
设置与关于面板模块。

包含两部分：
- 开机自启的跨平台读写（Windows 任务计划 / 注册表，macOS LaunchAgent）；
- 移植自 BASpark C# 版的设置面板 UI（自绘 Switch 开关与 Sparkle 星标）。
"""
import sys
import os
import plistlib
from pathlib import Path

from PySide6.QtWidgets import (
    QMainWindow, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
    QStackedWidget, QFrame, QButtonGroup, QMessageBox, QGraphicsDropShadowEffect,
    QAbstractButton
)
from PySide6.QtCore import Qt, QSize, Property, QRect, QPropertyAnimation
from PySide6.QtGui import QIcon, QPainter, QColor, QBrush, QPainterPath

from utils.helpers import get_resource_path


def get_autostart_cmd() -> list[str]:
    """获取自启动所需的命令行参数列表，兼容开发模式与打包模式。"""
    if getattr(sys, 'frozen', False):
        # 打包模式：直接启动可执行程序
        exe_path = sys.executable
        # macOS 下若可执行文件位于 .app 包内，改用 `open` 启动整个 bundle，
        # 确保应用获得正确的 Info.plist 上下文（激活策略 / Dock 行为）
        if sys.platform == "darwin":
            for parent in Path(exe_path).parents:
                if parent.suffix == ".app":
                    return ["open", str(parent), "--args", "--autostart"]
        return [exe_path, "--autostart"]
    else:
        # 开发模式：用 python 解释器启动入口脚本
        main_py = os.path.abspath(sys.argv[0])
        return [sys.executable, main_py, "--autostart"]


def set_autostart(enabled: bool) -> bool:
    """写入或删除系统开机自启项。

    - Windows：管理员权限下用任务计划（最高权限）启动，否则退回注册表 Run 项；
    - macOS：写入 ~/Library/LaunchAgents 下的 plist。

    两种 Windows 方式互斥，启用其一时会清理另一种，避免重复启动。
    """
    try:
        if sys.platform == "win32":
            import subprocess
            import ctypes
            import winreg

            cmd = get_autostart_cmd()
            if getattr(sys, 'frozen', False):
                exe_path = cmd[0]
                task_tr = f'"{exe_path}" --autostart'
            else:
                python_path = cmd[0]
                script_path = cmd[1]
                task_tr = f'"{python_path}" "{script_path}" --autostart'

            task_name = "BASparkAutoStart"
            reg_key_path = r"Software\Microsoft\Windows\CurrentVersion\Run"
            reg_val_name = "BASpark"

            # 判断当前进程是否拥有管理员权限
            try:
                is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            except Exception:
                is_admin = False

            # 以列表参数调用 schtasks，由 subprocess 按 Windows 规则正确加引号，
            # 避免含空格路径在 shell 拼接时引号嵌套出错；CREATE_NO_WINDOW 隐藏控制台
            def run_schtasks(args):
                return subprocess.run(
                    ["schtasks", *args],
                    creationflags=subprocess.CREATE_NO_WINDOW,
                    capture_output=True, text=True,
                )

            if enabled:
                if is_admin:
                    # 管理员权限：用任务计划以最高权限 (/rl highest) 启动，绕过 UAC 拦截
                    run_schtasks(["/create", "/tn", task_name, "/tr", task_tr,
                                  "/sc", "onlogon", "/rl", "highest", "/f"])

                    # 同步清理注册表 Run 项，避免与任务计划重复启动
                    try:
                        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_key_path, 0, winreg.KEY_SET_VALUE)
                        winreg.DeleteValue(key, reg_val_name)
                        winreg.CloseKey(key)
                    except FileNotFoundError:
                        pass
                else:
                    # 非管理员权限：退回传统的注册表 Run 项自启
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_key_path, 0, winreg.KEY_SET_VALUE)
                    winreg.SetValueEx(key, reg_val_name, 0, winreg.REG_SZ, task_tr)
                    winreg.CloseKey(key)

                    # 同步清理任务计划，避免与注册表项重复启动
                    run_schtasks(["/delete", "/tn", task_name, "/f"])
                return True
            else:
                # 禁用自启：注册表与任务计划两种方式都清理，确保彻底移除
                try:
                    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, reg_key_path, 0, winreg.KEY_SET_VALUE)
                    winreg.DeleteValue(key, reg_val_name)
                    winreg.CloseKey(key)
                except FileNotFoundError:
                    pass

                run_schtasks(["/delete", "/tn", task_name, "/f"])
                return True

        elif sys.platform == "darwin":
            plist_path = Path.home() / "Library" / "LaunchAgents" / "com.kalinrin.baspark.plist"
            if enabled:
                plist_path.parent.mkdir(parents=True, exist_ok=True)
                cmd = get_autostart_cmd()
                plist_data = {
                    "Label": "com.kalinrin.baspark",
                    "ProgramArguments": cmd,
                    "RunAtLoad": True,
                    "LimitLoadToSessionType": "Aqua"
                }
                with open(plist_path, "wb") as f:
                    plistlib.dump(plist_data, f)
            else:
                if plist_path.exists():
                    plist_path.unlink()
            return True
        else:
            return False
    except Exception as e:
        print(f"设置开机自启失败: {e}")
        return False


def check_autostart() -> bool:
    """检查系统中是否已配置自启动（Windows 同时探测注册表与任务计划）。"""
    try:
        if sys.platform == "win32":
            import subprocess
            import winreg

            # 1. 注册表 Run 项中是否存在 BASpark
            reg_exists = False
            try:
                key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_READ)
                winreg.QueryValueEx(key, "BASpark")
                winreg.CloseKey(key)
                reg_exists = True
            except FileNotFoundError:
                reg_exists = False
            except Exception:
                reg_exists = False

            # 2. 任务计划中是否存在高权限自启任务
            task_name = "BASparkAutoStart"
            result = subprocess.run(
                ["schtasks", "/query", "/tn", task_name],
                creationflags=subprocess.CREATE_NO_WINDOW,
                capture_output=True, text=True,
            )
            task_exists = (result.returncode == 0)

            # 任一方式生效即视为已启用
            return reg_exists or task_exists

        elif sys.platform == "darwin":
            plist_path = Path.home() / "Library" / "LaunchAgents" / "com.kalinrin.baspark.plist"
            return plist_path.exists()

        return False
    except Exception as e:
        print(f"检查自启动状态失败: {e}")
        return False


class SwitchButton(QAbstractButton):
    """仿《蔚蓝档案》风格的自绘开关，切换时带滑块平移动画。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(40, 20)
        self._offset = 3.0  # 滑块圆点的水平位置（px）
        self._anim = QPropertyAnimation(self, b"offset")
        self._anim.setDuration(150)

    @Property(float)
    def offset(self):
        """滑块圆点的水平位置（px），作为动画驱动属性。"""
        return self._offset

    @offset.setter
    def offset(self, val):
        self._offset = val
        self.update()

    def sizeHint(self):
        return QSize(40, 20)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # 胶囊状背景：选中为主题蓝，未选中为浅灰
        bg_color = QColor("#4CA7FF") if self.isChecked() else QColor("#DDD")
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(bg_color))
        rect = QRect(0, 0, 40, 20)
        painter.drawRoundedRect(rect, 10, 10)

        # 滑块白点，水平位置由 self._offset 决定
        painter.setBrush(QBrush(QColor("#FFFFFF")))
        dot_rect = QRect(int(self._offset), 3, 14, 14)
        painter.drawEllipse(dot_rect)

    def setChecked(self, checked):
        """程序化设置状态：直接定位滑块，不播放动画。"""
        super().setChecked(checked)
        self._anim.stop()
        self._offset = 23.0 if checked else 3.0
        self.update()

    def nextCheckState(self):
        """用户点击切换：从滑块当前位置平移到目标位置。"""
        super().nextCheckState()
        # 起点取当前真实位置，兼容动画播放途中再次点击的情形
        end = 23.0 if self.isChecked() else 3.0
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(end)
        self._anim.start()


class SparkleLogoWidget(QWidget):
    """自绘的《蔚蓝档案》双十字星标。

    macOS 上无法把 ✨ emoji 渲染成主题蓝，故改用矢量绘制以保证配色一致。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 28)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Arona 主题蓝
        color = QColor("#4CA7FF")
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(color))

        # 每个星形由 4 段二次贝塞尔组成，控制点取在中心，得到四角内凹的尖角
        # 1. 大星星（左上）
        cx1, cy1 = 11.0, 11.0
        R1 = 10.0
        path1 = QPainterPath()
        path1.moveTo(cx1, cy1 - R1)
        path1.quadTo(cx1, cy1, cx1 + R1, cy1)
        path1.quadTo(cx1, cy1, cx1, cy1 + R1)
        path1.quadTo(cx1, cy1, cx1 - R1, cy1)
        path1.quadTo(cx1, cy1, cx1, cy1 - R1)
        painter.drawPath(path1)

        # 2. 小星星（右下）
        cx2, cy2 = 21.0, 20.0
        R2 = 5.5
        path2 = QPainterPath()
        path2.moveTo(cx2, cy2 - R2)
        path2.quadTo(cx2, cy2, cx2 + R2, cy2)
        path2.quadTo(cx2, cy2, cx2, cy2 + R2)
        path2.quadTo(cx2, cy2, cx2 - R2, cy2)
        path2.quadTo(cx2, cy2, cx2, cy2 - R2)
        painter.drawPath(path2)


class SettingsWindow(QMainWindow):
    """设置与关于面板主窗口。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("BASpark - 控制面板")
        self.setFixedSize(680, 550)

        # 设置窗口图标
        icon_path = get_resource_path("app.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        self._init_ui()
        self._load_settings()

    def _init_ui(self):
        """构建左侧导航栏 + 右侧内容区（设置 / 关于两页）的整体布局。"""
        # 主框架容器
        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 1. 左侧侧边栏
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(180)
        sidebar.setStyleSheet("""
            QWidget#Sidebar {
                background-color: #FFFFFF;
                border-right: 1px solid #EAECEF;
            }
            QLabel {
                border: none;
                background: transparent;
            }
            QPushButton {
                border: none;
                border-radius: 5px;
                background-color: transparent;
                color: #666666;
                font-size: 14px;
                font-weight: bold;
                height: 40px;
                margin: 2px 10px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: #E0F2FF;
                color: #4CA7FF;
            }
            QPushButton:checked {
                background-color: #4CA7FF;
                color: #FFFFFF;
            }
            QPushButton:pressed {
                background-color: #3C97EF;
            }
        """)

        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 30, 0, 12)
        sidebar_layout.setSpacing(5)

        # 侧边栏标题：星标 + “BASpark” 文字
        logo_container = QWidget()
        logo_container.setObjectName("LogoContainer")
        logo_container.setStyleSheet("QWidget#LogoContainer { background: transparent; margin-bottom: 25px; }")
        logo_layout = QHBoxLayout(logo_container)
        logo_layout.setContentsMargins(15, 0, 15, 0)
        logo_layout.setSpacing(6)
        logo_layout.setAlignment(Qt.AlignCenter)

        # 矢量绘制的星标，替代无法在 macOS 着色的 ✨ emoji
        lbl_logo_pic = SparkleLogoWidget()
        logo_layout.addWidget(lbl_logo_pic)

        lbl_logo_text = QLabel("BASpark")
        lbl_logo_text.setStyleSheet("font-size: 20px; font-weight: bold; color: #4CA7FF; background: transparent; border: none;")
        logo_layout.addWidget(lbl_logo_text)

        sidebar_layout.addWidget(logo_container)

        # 侧边栏导航按钮
        self.btn_group = QButtonGroup(self)
        self.btn_group.setExclusive(True)

        self.btn_settings = QPushButton("设置")
        self.btn_settings.setCheckable(True)
        self.btn_group.addButton(self.btn_settings, 0)
        sidebar_layout.addWidget(self.btn_settings)

        self.btn_about = QPushButton("关于")
        self.btn_about.setCheckable(True)
        self.btn_group.addButton(self.btn_about, 1)
        sidebar_layout.addWidget(self.btn_about)

        sidebar_layout.addStretch()

        # 侧边栏底部版本与版权
        footer_layout = QVBoxLayout()
        footer_layout.setSpacing(2)
        footer_layout.setContentsMargins(0, 0, 0, 10)

        lbl_ver = QLabel("BASpark V1.2.0")
        lbl_ver.setAlignment(Qt.AlignCenter)
        lbl_ver.setStyleSheet("color: #B0B8C3; font-size: 10px;")

        lbl_copyright = QLabel("Copyright © 2026 kalinrin")
        lbl_copyright.setAlignment(Qt.AlignCenter)
        lbl_copyright.setStyleSheet("color: #A0A8B3; font-size: 10px;")

        footer_layout.addWidget(lbl_ver)
        footer_layout.addWidget(lbl_copyright)
        sidebar_layout.addLayout(footer_layout)

        main_layout.addWidget(sidebar)

        # 2. 右侧内容区 (StackedWidget)
        self.stacked = QStackedWidget()
        self.stacked.setObjectName("RightStacked")
        self.stacked.setStyleSheet("QWidget#RightStacked { background-color: #F5F7FA; }")

        # 2.1 设置页面
        page_settings = QWidget()
        page_settings_layout = QVBoxLayout(page_settings)
        page_settings_layout.setContentsMargins(30, 30, 30, 30)
        page_settings_layout.setSpacing(0)

        # 顶部配置栏 (标题 + 应用更改按钮)
        header_widget = QWidget()
        header_widget.setStyleSheet("background: transparent;")
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(0, 0, 0, 16)

        # 底部下划线效果
        header_border_widget = QWidget()
        header_border_widget.setObjectName("HeaderBorder")
        header_border_widget.setStyleSheet("QWidget#HeaderBorder { border-bottom: 1px solid #E3E8EF; background: transparent; }")
        header_border_layout = QVBoxLayout(header_border_widget)
        header_border_layout.setContentsMargins(0, 0, 0, 0)
        header_border_layout.addLayout(header_layout)

        title_sub_layout = QVBoxLayout()
        title_sub_layout.setSpacing(4)

        lbl_title = QLabel("参数配置")
        lbl_title.setStyleSheet("font-size: 24px; font-weight: bold; color: #333333; border: none; background: transparent;")

        lbl_sub = QLabel("*修改设置后，请点击右侧 [应用更改] 按钮生效。")
        lbl_sub.setStyleSheet("font-size: 11px; font-weight: bold; color: #FF6B6B; border: none; background: transparent;")

        title_sub_layout.addWidget(lbl_title)
        title_sub_layout.addWidget(lbl_sub)
        header_layout.addLayout(title_sub_layout)
        header_layout.addStretch()

        self.btn_apply = QPushButton("应用更改")
        self.btn_apply.setObjectName("btn_apply")
        self.btn_apply.setFixedSize(120, 36)
        self.btn_apply.setCursor(Qt.PointingHandCursor)
        self.btn_apply.setStyleSheet("""
            QPushButton#btn_apply {
                border: none;
                border-radius: 18px;
                background-color: #4CA7FF;
                color: #FFFFFF;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton#btn_apply:hover {
                background-color: #3C97EF;
            }
            QPushButton#btn_apply:pressed {
                background-color: #2C87DF;
            }
        """)
        # 按钮阴影效果
        shadow = QGraphicsDropShadowEffect(self.btn_apply)
        shadow.setBlurRadius(8)
        shadow.setXOffset(0)
        shadow.setYOffset(1)
        shadow.setColor(QColor(0, 0, 0, int(255 * 0.15)))
        self.btn_apply.setGraphicsEffect(shadow)

        header_layout.addWidget(self.btn_apply)
        page_settings_layout.addWidget(header_widget)
        page_settings_layout.addWidget(header_border_widget)

        page_settings_layout.addSpacing(20)

        # 配置白卡 QFrame
        card_frame = QFrame()
        card_frame.setStyleSheet("""
            QFrame {
                background-color: #FFFFFF;
                border-radius: 12px;
                border: 1px solid #EAECEF;
            }
            QLabel {
                border: none;
                background: transparent;
            }
        """)
        card_layout = QVBoxLayout(card_frame)
        card_layout.setContentsMargins(16, 16, 16, 16)
        card_layout.setSpacing(0)

        # 基础设置标签
        lbl_card_title = QLabel("基础设置")
        lbl_card_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #4CA7FF; margin-bottom: 12px;")
        card_layout.addWidget(lbl_card_title)

        # 开机自启选项行 (Switch + 文字描述)
        switch_row = QHBoxLayout()
        switch_row.setContentsMargins(0, 0, 0, 0)
        switch_row.setSpacing(10)

        self.switch_autostart = SwitchButton()
        switch_row.addWidget(self.switch_autostart)

        lbl_autostart_text = QLabel("开机自动启动")
        lbl_autostart_text.setStyleSheet("font-size: 14px; color: #333333; font-weight: bold;")
        switch_row.addWidget(lbl_autostart_text)
        switch_row.addStretch()

        card_layout.addLayout(switch_row)
        card_layout.addStretch()

        page_settings_layout.addWidget(card_frame)
        page_settings_layout.addStretch()  # 将卡片顶上去

        self.stacked.addWidget(page_settings)

        # 2.2 关于页面
        page_about = QWidget()
        page_about_layout = QVBoxLayout(page_about)
        page_about_layout.setContentsMargins(30, 40, 30, 30)
        page_about_layout.setSpacing(0)

        lbl_about_title = QLabel("关于")
        lbl_about_title.setStyleSheet("font-size: 24px; font-weight: bold; color: #333333; background: transparent; margin-bottom: 20px;")
        page_about_layout.addWidget(lbl_about_title)

        # 版本号文字
        lbl_about_ver = QLabel()
        lbl_about_ver.setText('<span style="font-size: 28px; font-weight: bold; color: #111111;">BASpark </span><span style="font-size: 24px; font-weight: bold; color: #4CA7FF;">V1.2.0</span>')
        lbl_about_ver.setStyleSheet("background: transparent; margin-bottom: 14px;")
        page_about_layout.addWidget(lbl_about_ver)

        # 描述
        lbl_about_desc = QLabel("深度复刻《蔚蓝档案》UI风格动效的鼠标特效工具")
        lbl_about_desc.setStyleSheet("font-size: 14px; color: #666666; background: transparent;")
        lbl_about_desc.setWordWrap(True)
        page_about_layout.addWidget(lbl_about_desc)

        page_about_layout.addStretch()  # 将关于页面内容顶上去

        self.stacked.addWidget(page_about)

        main_layout.addWidget(self.stacked)

        # 3. 关联事件
        self.btn_settings.clicked.connect(lambda: self.stacked.setCurrentIndex(0))
        self.btn_about.clicked.connect(lambda: self.stacked.setCurrentIndex(1))
        self.btn_apply.clicked.connect(self._apply_changes)

    def show_about_page(self):
        """选中并展示关于页面。"""
        self.btn_about.setChecked(True)
        self.stacked.setCurrentIndex(1)

    def _load_settings(self):
        """读取系统中实际的自启动状态，并同步到 Switch 开关。"""
        is_active = check_autostart()
        self.switch_autostart.setChecked(is_active)

    def _apply_changes(self):
        """应用更改：按开关状态写入/移除自启动，并弹出结果提示。"""
        enabled = self.switch_autostart.isChecked()
        success = set_autostart(enabled)

        if success:
            self._show_message("提示", "应用更改成功！", "#4CA7FF", "#3C97EF")
        else:
            self._show_message("错误", "应用更改失败，请检查系统权限。",
                               "#CC0000", "#BB0000", text_color="#CC0000")

    def _show_message(self, title, text, accent, accent_hover, text_color="#333333"):
        """弹出风格统一的消息框。

        Args:
            title: 标题栏文字。
            text: 正文内容。
            accent: 按钮底色。
            accent_hover: 按钮悬停色。
            text_color: 正文文字颜色，默认深灰。
        """
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(text)
        box.setStyleSheet(f"""
            QMessageBox {{
                background-color: #FFFFFF;
            }}
            QLabel {{
                font-size: 14px;
                color: {text_color};
                font-weight: bold;
            }}
            QPushButton {{
                border: none;
                border-radius: 4px;
                background-color: {accent};
                color: #FFFFFF;
                font-size: 12px;
                font-weight: bold;
                width: 80px;
                height: 28px;
            }}
            QPushButton:hover {{
                background-color: {accent_hover};
            }}
        """)
        box.exec()
