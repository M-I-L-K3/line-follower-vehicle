cmake_minimum_required(VERSION 3.14)
project(serial_daemon CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# 固定版本拉取 CSerialPort v4.3.3(4.x 末代稳定版), 保证构建可复现:
# 本项目用到的 API (init/setDataBits/setParity/setStopBits/open/
# close/writeData/getLastError) 在该版本全部存在。
# 如离线构建, 可改为本地源码:
#   git clone -b v4.3.3 https://github.com/itas109/CSerialPort third_party/CSerialPort
#   add_subdirectory(third_party/CSerialPort)
include(FetchContent)
FetchContent_Declare(
  CSerialPort
  GIT_REPOSITORY https://github.com/itas109/CSerialPort.git
  GIT_TAG        v4.3.3
)
FetchContent_MakeAvailable(CSerialPort)

add_executable(serial_daemon main.cpp)
target_link_libraries(serial_daemon PRIVATE CSerialPort)
