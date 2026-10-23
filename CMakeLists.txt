cmake_minimum_required(VERSION 3.20)
project(MCP_Standalone VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# 查找依赖
find_package(PkgConfig REQUIRED)
find_package(CURL REQUIRED)
find_package(Threads REQUIRED)

# 公共组件
add_subdirectory(common)

# MCP 客户端库（含 RAG-MCP）
add_subdirectory(mcp_client)

# MCP 服务器
add_subdirectory(mcp_server)

# 示例程序
option(BUILD_MCP_EXAMPLES "Build MCP examples" ON)
if(BUILD_MCP_EXAMPLES)
    add_subdirectory(examples)
endif()
