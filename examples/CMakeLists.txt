cmake_minimum_required(VERSION 3.20)

# RAG-MCP 示例
add_executable(rag_mcp_example rag_mcp_example.cpp)
target_link_libraries(rag_mcp_example PRIVATE
    mcp_client
    mcp_common
)
target_compile_features(rag_mcp_example PRIVATE cxx_std_20)

# AI-MCP 集成示例 - 需要 ai_interface 模块，当前未包含在此子项目中
# add_executable(ai_mcp_integration_example ai_mcp_integration_example.cpp)
# target_link_libraries(ai_mcp_integration_example PRIVATE
#     mcp_client
#     mcp_common
# )
# target_compile_features(ai_mcp_integration_example PRIVATE cxx_std_20)
