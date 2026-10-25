"""
config.py 参考模板 -- 只示范 ENABLE_TURN_ANGLE_UPDATE 的安全解析写法。
请把 _env_int 辅助函数与该变量的读取方式合并进项目真实的 config.py,
其余配置项(SCREEN_WIDTH/FRAME_OUTPUT_METHOD/OPENCV_DETECT_ON 等)保持原样。
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int = 1) -> int:
    """读取整型环境变量; 变量缺失或为空字符串时返回默认值, 不抛 ValueError"""
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


# send_command() 的总开关(转角/速度/循迹命令全部受控): 1=发送, 0=停发
ENABLE_TURN_ANGLE_UPDATE = _env_int("ENABLE_TURN_ANGLE_UPDATE", 1)

# 画面输出方式: 0=无输出; 1=本地录像(output.avi); 2=OpenCV 窗口预览
FRAME_OUTPUT_METHOD = _env_int("FRAME_OUTPUT_METHOD", 2)
