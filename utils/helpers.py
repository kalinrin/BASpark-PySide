"""
通用工具模块，提供路径解析与单实例锁功能。
"""
import sys
from pathlib import Path
from PySide6.QtNetwork import QLocalSocket, QLocalServer


def get_resource_path(relative_path: str) -> Path:
    """
    获取资源文件的绝对路径。

    兼容开发环境与 PyInstaller 打包后的 _MEIPASS 临时目录机制。

    Args:
        relative_path (str): 相对于项目根目录的相对路径。

    Returns:
        Path: 资源文件的绝对路径对象。
    """
    base_path = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    return base_path / relative_path


def check_single_instance(server_name: str = "BASpark_SingleInstance_Server_Lock") -> QLocalServer | None:
    """
    检查程序是否已经存在运行中的实例（基于 QLocalSocket IPC 机制）。

    Args:
        server_name (str): 用于进程间通信的本地服务名称。

    Returns:
        QLocalServer | None: 如果是唯一实例，返回 server 对象以保持锁定；否则返回 None。
    """
    socket = QLocalSocket()
    socket.connectToServer(server_name)

    if socket.waitForConnected(500):
        return None  # 已经有实例在运行

    local_server = QLocalServer()
    local_server.removeServer(server_name)
    local_server.listen(server_name)
    return local_server