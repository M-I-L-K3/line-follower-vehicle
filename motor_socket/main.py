from dotenv import load_dotenv

# curve_detector 当前仅保留导入未调用(有意保留, 接入曲率方案见项目说明)
from vision import curve_detector, light_detect, track_line
load_dotenv()  # 必须在所有导入之前加载 .env 文件

import cv2
import socket
import threading
import signal
import serial_pi.motor as motor
import config

UPTIME_START_WHEN = 0

# C++ 串口守护进程(CSerialPort)地址
DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 8080

def nothing(x):
    pass

shutdown_flag = threading.Event()

def signal_handler(signum, frame):
    """信号处理器 for Ctrl+C: 只置退出标志, 资源统一由 finally 清理"""
    print("\nReceived exit signal (Ctrl+C), shutting down gracefully...")
    shutdown_flag.set()

def check_serial_daemon(timeout: float = 2.0) -> bool:
    """启动前检查 C++ 串口守护进程是否在 127.0.0.1:8080 监听"""
    try:
        probe = socket.create_connection((DAEMON_HOST, DAEMON_PORT), timeout=timeout)
        probe.close()
        return True
    except Exception:
        return False

def main():
    # 所有会发送命令的模式都需要守护进程
    needs_serial = (config.FRAME_OUTPUT_METHOD in (0, 1)) or config.OPENCV_DETECT_ON
    if needs_serial:
        if not check_serial_daemon():
            print("Serial daemon (CSerialPort) not running on 127.0.0.1:8080")
            exit(1)
        print("Serial daemon (CSerialPort) connected")

    # 注册信号处理器: Ctrl+C 只置退出标志, 资源统一由 finally 清理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    cap = cv2.VideoCapture(0)
    # cap = cv2.VideoCapture("/Users/lingc/Documents/output1.avi")
    # cap.set(cv2.CAP_PROP_BRIGHTNESS, 0.5)
    # cap.set(cv2.CAP_PROP_CONTRAST, 0.6)
    # cap.set(cv2.CAP_PROP_SATURATION, 3)
    actual_fps = cap.get(cv2.CAP_PROP_FPS)
    print(f"Camera actual FPS: {actual_fps}")

    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    # FRAME_OUTPUT_METHOD 现状(默认 2, 见 config):
    #   0 = 无输出; 1 = 本地录像输出(output.avi, 原 HTTP 推流已移除);
    #   2 = OpenCV 窗口实时预览
    local_output = config.RECORD_VIDEO or config.FRAME_OUTPUT_METHOD == 1
    out = None
    if local_output:
        fps = actual_fps if actual_fps and actual_fps > 0 else 30
        out = cv2.VideoWriter('output.avi', cv2.VideoWriter_fourcc(*'MJPG'),
                              fps, (config.SCREEN_WIDTH, config.SCREEN_HEIGHT))

    if config.SHOW_TRACKBAR:
        cv2.namedWindow("Video Trackbar", cv2.WINDOW_NORMAL)
        cv2.createTrackbar("H Lower", "Video Trackbar", 10, 179, nothing)
        cv2.createTrackbar("H Upper", "Video Trackbar", 40, 179, nothing)
        cv2.createTrackbar("S Lower", "Video Trackbar", 40, 255, nothing)
        cv2.createTrackbar("S Upper", "Video Trackbar", 255, 255, nothing)
        cv2.createTrackbar("V Lower", "Video Trackbar", 120, 255, nothing)
        cv2.createTrackbar("V Upper", "Video Trackbar", 255, 255, nothing)

    try:
        while not shutdown_flag.is_set():
            ret, frame = cap.read()
            if not ret:
                break

            r_frame = cv2.resize(frame, (config.SCREEN_WIDTH, config.SCREEN_HEIGHT))

            if config.OPENCV_DETECT_ON:
                direction, error = track_line.handle_one_frame(r_frame, config.SCREEN_HEIGHT)

                # 统一使用 r_frame: 红绿灯阈值需按 SCREEN_WIDTH x SCREEN_HEIGHT 调试,
                # 若 light_detect 的阈值是按原始分辨率调的, 请把这两行改回 frame
                redCount, greenCount = light_detect.handle_lights(r_frame)

                signal_v, signal_cmd = light_detect.process_signal(r_frame, redCount, greenCount)

                  # if signal_v == 0:
                #     cv2.putText(frame, f"red light {redCount}/{greenCount}", (10, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 1)
                # elif signal_v == 1:
                #     cv2.putText(frame, f"green light {redCount}/{greenCount}", (10, 42), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 1)

                command = f"cv:{error},{signal_cmd}\n"
                motor.get_motor_controller().send_command(command)

                cv2.putText(frame, f"dir: {direction}", (10, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.4,(155,55,0), 1)
                cv2.putText(frame, f"error: {error}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 30), 1)

            if out is not None:
                # 写入 r_frame: 与 VideoWriter 的输出尺寸一致(原来写 frame 会导致尺寸不匹配)
                out.write(r_frame)

            if(config.FRAME_OUTPUT_METHOD == 2):
                cv2.imshow("Original", frame)
                # cv2.imshow("Track Line", yellow_mask)

            # 按'q'退出
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
    finally:
        # 清理资源(Ctrl+C 经 shutdown_flag 退出后也走这里, 只清理一次)
        cap.release()
        if out is not None:
            out.release()
        cv2.destroyAllWindows()

if __name__ == "__main__":
    main()
