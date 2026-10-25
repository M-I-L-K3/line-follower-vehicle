from typing import Dict, Any
import socket
import time
import config

# 注意: config.ENABLE_TURN_ANGLE_UPDATE 是 send_command() 的总开关,
# 同时控制转角(cv:)、速度(LS:/RS:)和循迹等所有下发命令,
# 设为 0 会停发全部指令; 目前不拆分为多个独立开关, 如有需求再拆。

# TCP 服务器（C++ 守护进程）地址
TCP_HOST = "127.0.0.1"
TCP_PORT = 8080

# 发送失败时的日志限流: 最多每 2 秒打印一次
ERROR_LOG_INTERVAL = 2.0


class Motor_Controller:
    """电机控制: 通过 TCP socket 把命令发给本机 C++ 串口守护进程"""

    def __init__(self):
        self._sock: socket.socket = None
        self._addr = (TCP_HOST, TCP_PORT)
        self._fail_count = 0        # 连续失败次数
        self._last_err_ts = 0.0     # 上次打印错误的时间

    def _get_socket(self) -> socket.socket:
        """获取已连接的 socket, 断线时自动重连"""
        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(2.0)
            self._sock.connect(self._addr)
        return self._sock

    def _log_error(self, msg: str):
        """限流打印错误: 连续失败期间每 2 秒最多一条"""
        self._fail_count += 1
        now = time.monotonic()
        if self._fail_count == 1 or (now - self._last_err_ts) >= ERROR_LOG_INTERVAL:
            print(f"{msg} (consecutive failures: {self._fail_count})")
            self._last_err_ts = now

    def send_command(self, cmd: str):
        """
        发送一条命令给串口守护进程
        对外接口不变: 内部由 pyserial 串口改为 TCP socket,
        消息以 \n 结尾作为分隔符, 组帧与串口发送由 C++ 端完成
        """
        try:
            if config.ENABLE_TURN_ANGLE_UPDATE:
                payload = cmd.rstrip("\n") + "\n"
                sock = self._get_socket()
                sock.sendall(payload.encode("ascii"))
                if self._fail_count > 0:
                    print(f"Send reconnected after {self._fail_count} failures")
                self._fail_count = 0
        except Exception as e:
            # 连接失败时丢弃旧 socket, 下次调用自动重连
            self._log_error(f"Send command failed: {e}")
            if self._sock is not None:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None

    def send_turn_angle(self, angle: int):
        try:
            if config.ENABLE_TURN_ANGLE_UPDATE:
                self.send_command(f"cv:{angle}\n")
        except Exception as e:
            self._log_error(f"Send turn angle failed: {e}")

    def set_motor_speed(self, left_speed: int, right_speed: int) -> Dict[str, Any]:
        """
        设置左右电机速度
        Args:
            left_speed: 左电机速度 (-100到100)
            right_speed: 右电机速度 (-100到100)
        Returns:
            执行结果
        """
        self.send_command(f"LS:{left_speed},RS:{right_speed}\n")
        return None


motor_controller = Motor_Controller()


def get_motor_controller() -> Motor_Controller:
    return motor_controller
