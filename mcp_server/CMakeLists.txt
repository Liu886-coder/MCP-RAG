cmake_minimum_required(VERSION 3.20)
project(mcp_server)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED True)

# 设置版本号
set(PROJECT_VERSION_MAJOR 0)
set(PROJECT_VERSION_MINOR 7)
set(PROJECT_VERSION_PATCH 0)
set(PROJECT_VERSION "${PROJECT_VERSION_MAJOR}.${PROJECT_VERSION_MINOR}.${PROJECT_VERSION_PATCH}")

# 生成 version.h
configure_file(
    "${CMAKE_CURRENT_SOURCE_DIR}/version.h.in"
    "${CMAKE_CURRENT_BINARY_DIR}/version.h"
)

find_package(Threads REQUIRED)

set(SOURCES
    src/main.cpp
    src/server/Server.cpp
    src/transport/StdioTransport.cpp
    src/transport/HttpStreamTransport.cpp
    src/transport/SseTransport.cpp
    src/loader/PluginsLoader.cpp
)

add_executable(${PROJECT_NAME} ${SOURCES})
target_include_directories(${PROJECT_NAME} PRIVATE
    include
    src
    src/transport
    src/interface
    libs_tier_01/aixlog-1.5.0/include
    ${CMAKE_CURRENT_BINARY_DIR}
)

target_link_libraries(${PROJECT_NAME} PRIVATE Threads::Threads)

# 平台相关链接
if(WIN32)
    target_link_libraries(${PROJECT_NAME} PRIVATE ws2_32)
else()
    # POSIX 系统通常不需要额外链接
endif()

if(CMAKE_COMPILER_IS_GNUCC OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
    if(WIN32)
        target_link_options(${PROJECT_NAME} PRIVATE "-static-libgcc" "-static-libstdc++" "-static")
    elseif(UNIX AND NOT APPLE)
        target_link_options(${PROJECT_NAME} PRIVATE "-static-libgcc" "-static-libstdc++")
    endif()
endif()

# 示例插件
add_subdirectory(plugins/weather)
add_subdirectory(plugins/sleep)
add_subdirectory(plugins/code-review)
add_subdirectory(plugins/bacio-quote)
add_subdirectory(plugins/notification)
add_subdirectory(plugins/calculator)
