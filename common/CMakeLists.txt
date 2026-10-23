cmake_minimum_required(VERSION 3.20)

# 公共组件库（轻量版 - 仅包含 MCP 所需的 logger 和 types）
set(COMMON_SOURCES
    src/logger.cpp
)

add_library(mcp_common STATIC ${COMMON_SOURCES})

target_include_directories(mcp_common PUBLIC
    ${CMAKE_CURRENT_SOURCE_DIR}/include
)

target_compile_features(mcp_common PRIVATE cxx_std_20)

target_compile_options(mcp_common PRIVATE -Wall -Wextra)

# 安装规则
install(TARGETS mcp_common
    ARCHIVE DESTINATION lib
    LIBRARY DESTINATION lib
)

install(DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/include/agent_rpc
    DESTINATION include
    FILES_MATCHING PATTERN "*.h"
)
