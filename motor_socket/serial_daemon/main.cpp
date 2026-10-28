// SPDX-License-Identifier: MIT
// serial_daemon: TCP 服务端(127.0.0.1:8080) -> CSerialPort -> STM32
// 串口参数: 115200, 8N1
//
// 帧格式与原 Python serial_io.send_command 逐字节一致:
//   payload  含末尾 \n, 与 Python 原版一致
//   data     = payload 的 ASCII 字节
//   length   = len(data) + 3 (含 header, length, checksum)
//   checksum = (0xAA + length + sum(data)) & 0xFF, sum 包含 \n
//   packet   = [0xAA][length] + data + [checksum]
//
// 编译: cmake -B build && cmake --build build
// CMake 通过 FetchContent 固定拉取 CSerialPort v4.3.3(4.x 末代稳定版,
// 本文件用到的 init/setDataBits/setParity/setStopBits/open/writeData/
// getLastError 接口在该版本全部存在; v5.0.0 为大版本重构, 未采用)
//
// 串口选择优先级: 环境变量 SERIAL_PORT > /dev/ttyAMA0 > 第一个可用端口
//
// 已知设计代价: open_serial() 有 2 秒重开节流(g_last_open_try),
// 串口闪断后补发最多延迟 2 秒; 如需缩短可把节流常量化, 默认不改。

#include "CSerialPort/SerialPort.h"
#include "CSerialPort/SerialPortInfo.h"

#include <sys/socket.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <poll.h>
#include <unistd.h>
#include <csignal>
#include <cstdio>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <ctime>
#include <string>
#include <vector>
#include <deque>
#include <atomic>

using namespace itas109;

static const int BAUDRATE = 115200;
static const int TCP_PORT = 8080;

// 串口端口: 优先环境变量 SERIAL_PORT, 其次 /dev/ttyAMA0
static std::string serial_port_env()
{
    if (const char *env = getenv("SERIAL_PORT"); env && env[0])//getenv是cstdlib中的函数，用于获取环境变量，SERTAL_PORT是树莓派到STM32的串口
        return std::string(env);
    return std::string("/dev/ttyAMA0");
}

static std::atomic<bool> g_running{true};//atomic是原子操作，这个bool类型可以被多个线程同时读
static CSerialPort g_serial;

// 串口断开期间的待发送队列(按序补发)
static std::deque<std::string> g_pending;//双端队列
static const size_t MAX_PENDING = 256;//size_t是无符号整数类型，头文件cstdint

// 串口重开尝试的节流(避免每次循环都打印打开失败)
static time_t g_last_open_try = 0;//time_t是存时间戳的类型，头文件ctime

static void on_sigint(int) { g_running = false; }

static bool open_serial()
{
    if (g_serial.isOpen())
        return true;

    // 节流: 最多每 2 秒尝试重开一次
    time_t now = time(nullptr);
//串口重开判断：小于2秒不重开
    if (now - g_last_open_try < 2)
        return false;
    g_last_open_try = now;

    std::string port = serial_port_env();
    if (access(port.c_str(), F_OK) != 0) {  //access判断路径是否存在，有没有访问权限。返回值0：成立，-1：失败;c_str是string的成员函数，能把string类型转化为char*类型；F_OK参数看文件是否存在
        std::vector<SerialPortInfo> list = CSerialPortInfo::availablePortInfos();//扫描所有可用串口并返回列表
        if (list.empty()) {
            printf("[serial] no available port (SERIAL_PORT=%s not found)\n", port.c_str());//%s是占位符，输出路径
            return false;
        }
        port = list[0].portName();//默认取第一个
        printf("[serial] %s not found, fallback to %s\n", serial_port_env().c_str(), port.c_str());//没找到，默认取第一个串口
    }

    g_serial.init(port.c_str(), BAUDRATE);
    // 8N1
    g_serial.setDataBits(DataBits8);
    g_serial.setParity(ParityNone);
    g_serial.setStopBits(StopOne);

    if (!g_serial.open()) {
        printf("[serial] open %s failed: %s\n", port.c_str(), g_serial.getLastError().message());
        return false;
    }
    printf("[serial] opened %s @ %d 8N1\n", port.c_str(), BAUDRATE);
    return true;
}

// 组帧: payload 包含末尾 \n, 与 Python 原版一致，格式为帧头 + 长度 +payload + 校验和
static std::vector<uint8_t> make_frame(const std::string &payload)
{
    const uint8_t header = 0xAA;
    const uint8_t length = static_cast<uint8_t>(payload.size() + 3);

    uint16_t checksum = header + length;
    for (unsigned char c : payload)
        checksum += c;
    checksum &= 0xFF;

    std::vector<uint8_t> frame;
    frame.reserve(payload.size() + 3);
    frame.push_back(header);
    frame.push_back(length);
    frame.insert(frame.end(), payload.begin(), payload.end());
    frame.push_back(static_cast<uint8_t>(checksum));
    return frame;
}

static void enqueue_pending(const std::string &line)
{
    if (g_pending.size() >= MAX_PENDING) {
        g_pending.pop_front(); // 队列满时丢最旧的一条
        printf("[queue] overflow, dropped oldest command\n");
    }
    g_pending.push_back(line);
}

// 把一行指令组帧后经串口发出; 串口不可用时入队暂存
static void send_line(const std::string &line)
{
    // 长度检查: length 是单字节, payload.size()+3 超过 255 会溢出产生损坏帧
    if (line.size() + 3 > 255) {
        printf("[serial] payload too long: %zu, dropped\n", line.size());
        return;
    }

    if (!open_serial()) {
        enqueue_pending(line);
        return;
    }

    std::vector<uint8_t> frame = make_frame(line);
    long n = g_serial.writeData(frame.data(), static_cast<long>(frame.size()));
    if (n < 0) {
        printf("[serial] write failed, reopening (command queued)\n");
        g_serial.close();
        enqueue_pending(line);
    }
}

// 串口恢复后按序补发积压命令
static void flush_pending()
{
    while (g_running && !g_pending.empty() && g_serial.isOpen()) {
        const std::string &line = g_pending.front();
        std::vector<uint8_t> frame = make_frame(line);
        long n = g_serial.writeData(frame.data(), static_cast<long>(frame.size()));
        if (n < 0) {
            printf("[serial] write failed during flush, reopening\n");
            g_serial.close();
            break;
        }
        g_pending.pop_front();
    }
}

int main()
{
    signal(SIGINT, on_sigint);
    signal(SIGTERM, on_sigint);

    // TCP 服务端: 只监听本机回环
    int server = socket(AF_INET, SOCK_STREAM, 0);
    if (server < 0) {
        perror("socket");
        return 1;
    }

    int opt = 1;
    setsockopt(server, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    sockaddr_in addr{};
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port        = htons(TCP_PORT);

    if (bind(server, reinterpret_cast<sockaddr *>(&addr), sizeof(addr)) < 0) {
        perror("bind");
        return 1;
    }
    if (listen(server, 1) < 0) {
        perror("listen");
        return 1;
    }
    printf("[tcp] listening on 127.0.0.1:%d\n", TCP_PORT);

    char buf[512];
    while (g_running) {
        // accept 用 poll 带超时, 保证 Ctrl+C 能及时退出
        struct pollfd pfd{};
        pfd.fd = server;
        pfd.events = POLLIN;
        int pr = poll(&pfd, 1, 500);
        if (pr <= 0)
            continue;

        sockaddr_in cli{};
        socklen_t cli_len = sizeof(cli);
        int conn = accept(server, reinterpret_cast<sockaddr *>(&cli), &cli_len);//接收通讯连接
        if (conn < 0) {
            if (g_running) perror("accept");
            break;
        }
        printf("[tcp] client connected\n");

        std::string line_buf;
        bool client_ok = true;
        while (g_running && client_ok) {
            // 串口恢复时按序补发积压命令
            if (open_serial())
                flush_pending();

            // recv 用 poll 带超时, 每次循环检查 g_running
            struct pollfd cfd{};
            cfd.fd = conn;
            cfd.events = POLLIN;
            int cr = poll(&cfd, 1, 200);
            if (cr < 0) {
                if (!g_running) break;
                perror("poll");
                client_ok = false;
                break;
            }
            if (cr == 0)
                continue; // 超时, 回到循环头检查退出标志

            ssize_t n = recv(conn, buf, sizeof(buf), 0);//接收数据
            if (n <= 0) {
                printf("[tcp] client disconnected\n");
                client_ok = false;
                break;
            }

            line_buf.append(buf, static_cast<size_t>(n));

            // 按行拆分, 每行一条指令(行尾 \n 保留, 进入组帧)
            size_t pos;
            while ((pos = line_buf.find('\n')) != std::string::npos) {
                std::string line = line_buf.substr(0, pos + 1); // 保留换行
                line_buf.erase(0, pos + 1);
                if (line.size() > 1)
                    send_line(line);
            }
        }
        close(conn);

        // 客户端断开期间串口若已恢复, 立即补发积压命令, 不等下一个客户端
        if (open_serial())
            flush_pending();
    }

    if (!g_pending.empty())
        printf("[exit] %zu queued commands discarded on shutdown\n", g_pending.size());
    g_serial.close();
    close(server);
    printf("\n[exit] daemon stopped\n");
    return 0;
}
