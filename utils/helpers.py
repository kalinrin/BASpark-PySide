"""
通用工具模块，提供资源路径解析与单实例锁功能。
"""
import sys
from pathlib import Path
from PySide6.QtNetwork import QLocalSocket, QLocalServer


def get_resource_path(relative_path: str) -> Path:
    """获取资源文件的绝对路径，兼容开发环境与 PyInstaller 打包。

    Args:
        relative_path (str): 相对于项目根目录的路径。

    Returns:
        Path: 资源文件的绝对路径。
    """
    # 打包后从 _MEIPASS 临时目录取资源，否则取项目根目录
    base_path = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    return base_path / relative_path


def check_single_instance(server_name: str = "BASpark_SingleInstance_Server_Lock") -> QLocalServer | None:
    """基于本地 socket 检查是否已有实例在运行。

    Args:
        server_name (str): 进程间通信使用的本地服务名。

    Returns:
        QLocalServer | None: 当前为唯一实例时返回 server（需持有引用以保持锁），
        否则返回 None。
    """
    socket = QLocalSocket()
    socket.connectToServer(server_name)

    # 能连接成功说明已有实例在运行
    if socket.waitForConnected(500):
        return None

    # 清理可能残留的旧锁（如上次异常退出），再开始监听
    local_server = QLocalServer()
    local_server.removeServer(server_name)
    local_server.listen(server_name)
    return local_server
