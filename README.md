# MCP (Model Context Protocol) 完整实现

MCP 协议的完整 C++ 实现，包含 MCP 客户端、MCP 服务器和 RAG-MCP 智能工具检索模块。

从 [agent-communication](../) 项目中独立提取的子项目。

## 项目结构

```
mcp_standalone/
├── CMakeLists.txt              # 根构建文件
├── README.md
├── common/                     # 公共组件（日志、类型定义）
│   ├── include/agent_rpc/common/
│   │   ├── types.h             # 通用类型定义
│   │   └── logger.h            # 日志系统接口
│   └── src/
│       └── logger.cpp          # 日志实现
├── mcp_client/                 # MCP 客户端库
│   ├── include/agent_rpc/mcp/
│   │   ├── mcp_client.h        # MCP 客户端接口（STDIO + SSE）
│   │   ├── mcp_agent_integration.h  # AI Agent 集成辅助类
│   │   └── rag/                # RAG-MCP 模块
│   │       ├── embedding_service.h  # DashScope 向量化服务
│   │       ├── embedding_cache.h    # LRU 向量缓存
│   │       ├── vector_index.h       # 向量索引
│   │       ├── tool_retriever.h     # 工具检索器
│   │       └── tool_validator.h     # 工具验证器
│   └── src/
│       ├── mcp_client.cpp           # 客户端实现
│       ├── mcp_tool_manager.cpp     # 工具管理器
│       ├── mcp_agent_integration.cpp # Agent 集成
│       └── rag/                     # RAG 实现
├── mcp_server/                 # MCP 服务器（独立可执行程序）
│   ├── src/
│   │   ├── main.cpp            # 服务器入口
│   │   ├── server/Server.{h,cpp}    # 核心服务器
│   │   ├── transport/          # 传输层
│   │   │   ├── StdioTransport  # 标准 I/O 传输
│   │   │   ├── SseTransport    # SSE 传输
│   │   │   └── HttpStreamTransport  # HTTP 流传输
│   │   ├── interface/          # 接口定义
│   │   │   ├── ITransport.h    # 传输接口
│   │   │   └── PluginAPI.h     # 插件 API
│   │   ├── loader/             # 插件加载器
│   │   └── utils/              # 工具类
│   ├── plugins/                # 内置插件
│   │   ├── calculator/         # 计算器
│   │   ├── weather/            # 天气查询
│   │   ├── sleep/              # 延时工具
│   │   ├── code-review/        # 代码审查
│   │   ├── notification/       # 通知
│   │   └── bacio-quote/        # 名言
│   └── test/                   # 测试脚本
├── examples/                   # 示例代码
│   ├── rag_mcp_example.cpp
│   └── ai_mcp_integration_example.cpp
└── docs/                       # 文档
    ├── mcp-plugin-development.md
    └── rag-mcp-guide.md
```

## 核心组件

### 1. MCP 客户端 (`mcp_client`)

MCP Client负责“发起调用”，MCP Server负责“提供工具并执行”
支持两种传输模式的 MCP 客户端：

- **STDIO 模式**: 通过标准输入输出与本地 MCP Server 进程通信
- **SSE 模式**: 通过 HTTP Server-Sent Events 与远程 MCP Server 通信

主要类：
- `MCPClient` - MCP 客户端（JSON-RPC 协议）
- `MCPToolManager` - 工具管理器
- `MCPServiceIntegrator` - 服务集成器
- `MCPAgentIntegration` - AI Agent 简化集成接口

### 2. RAG-MCP 模块

基于检索增强生成的智能工具选择：

- `EmbeddingService` - 阿里百炼 DashScope 文本向量化
- `EmbeddingCache` - LRU 缓存，减少 API 调用
- `VectorIndex` - 向量索引，余弦相似度搜索
- `ToolRetriever` - 工具检索器，整合以上组件
- `ToolValidator` - 可选的工具验证

### 3. MCP 服务器 (`mcp_server`)

完整的 MCP 服务器实现（MIT License, by Giuseppe Mastrangelo）：

- 插件化架构，支持动态加载 `.so` 插件
- 三种传输模式：STDIO / SSE / HTTP Stream
- 内置 6 个示例插件
- 跨平台支持（Linux / macOS / Windows）

## 依赖项

| 依赖 | 用途 | 模块 |
|------|------|------|
| C++20 编译器 | 编译 | 全部 |
| CMake >= 3.20 | 构建 | 全部 |
| libcurl | HTTP/SSE 客户端 | mcp_client |
| jsoncpp | JSON 解析 | mcp_client |
| nlohmann/json | JSON（header-only, 已内置） | mcp_server, mcp_client(RAG) |
| Threads | 多线程 | 全部 |

## 构建

```bash
mkdir build && cd build
cmake ..
make -j$(nproc)
```

### 仅构建 MCP 服务器

```bash
cd mcp_server
mkdir build && cd build
cmake ..
make -j$(nproc)
```

### 构建选项

```bash
# 不构建示例
cmake -DBUILD_MCP_EXAMPLES=OFF ..

# Debug 模式
cmake -DCMAKE_BUILD_TYPE=Debug ..
```

## 快速开始

### 启动 MCP 服务器

```bash
# STDIO 模式（默认）
./build/mcp_server/mcp_server -p ./build/mcp_server/plugins

# SSE 模式
./build/mcp_server/mcp_server -s -p ./build/mcp_server/plugins
```

### 使用 MCP 客户端

```cpp
#include "agent_rpc/mcp/mcp_client.h"

using namespace agent_rpc::mcp;

// STDIO 模式连接
MCPClient client;
client.connect("/path/to/mcp_server", {"--plugins", "./plugins"});

// 列出工具
auto tools = client.listTools();
for (const auto& tool : tools) {
    std::cout << tool.name << ": " << tool.description << std::endl;
}

// 调用工具
auto response = client.callTool("calculator", R"({"expression": "2+3"})");
std::cout << response.result << std::endl;

client.disconnect();
```

### 使用 RAG-MCP 智能工具检索

```cpp
#include "agent_rpc/mcp/mcp_agent_integration.h"

using namespace agent_rpc::mcp;

MCPAgentConfig config;
config.enable_mcp = true;
config.mcp_server_path = "/path/to/mcp_server";
config.rag_config.enabled = true;
config.rag_config.api_key = std::getenv("DASHSCOPE_API_KEY");

MCPAgentIntegration mcp;
mcp.initialize(config);

// 智能检索最相关的工具
auto tools = mcp.getRelevantTools("计算 123 + 456");

// 获取 LLM 函数调用格式
std::string json = mcp.getRelevantToolsAsJson("查询天气");
```

### 测试 MCP 服务器

统一入口脚本，支持两种测试模式：

```bash
./test_all.sh
```

运行后选择模式：

- **模式 1 — AI 集成测试（推荐）**：使用阿里百炼 AI 模型，测试完整 Agent 工具调用链路
- **模式 2 — 基础功能测试**：无需 API Key，仅验证 MCP Server 协议和插件

#### AI 集成测试

使用阿里百炼 (DashScope) 大模型作为 AI Agent，通过 MCP 协议调用工具，验证完整链路：

```
用户查询 → Embedding 向量化 → RAG 相似度检索 → 筛选相关工具 → LLM (工具选择) → MCP Server (工具执行) → LLM (生成回答)
```

**前置条件**：

- 阿里百炼 API Key（[申请地址](https://dashscope.console.aliyun.com/)）
- Python 3.6+（无需额外依赖）

```bash
# 交互式运行，终端输入 API Key
./test_all.sh
# 选择 1

# 或通过环境变量
DASHSCOPE_API_KEY="sk-xxx" python3 test_ai_mcp.py

# 指定模型
DASHSCOPE_API_KEY="sk-xxx" DASHSCOPE_MODEL="qwen-turbo" python3 test_ai_mcp.py
```

测试覆盖项：

| 步骤 | 测试内容 |
|------|----------|
| MCP 初始化 | 启动服务器，建立 STDIO 连接 |
| 工具发现 | 获取所有插件注册的工具列表 |
| RAG 索引构建 | 批量 Embedding 向量化所有工具，建立向量索引 |
| RAG + AI 交互式调用 | 用户查询 → 向量相似度筛选工具 → 仅相关工具发给 LLM → MCP 执行 → 回传结果 |
| 基础工具验证 | 直接调用 sleep、calculator、错误处理 |

#### 基础功能测试

无需 API Key，直接验证 MCP Server 协议正确性：

```bash
./test_all.sh
# 选择 2
```

覆盖：initialize、ping、tools/list、calculator、sleep、error handling。

### 使用 Python MCP Server (Windows / PyCharm)

`test_ai_mcp.py` 可以改为连接仓库内的 Python MCP Server，不需要运行 Linux 构建产物。
在 PyCharm 中为项目选择 Python 3.10 或更新版本的解释器，然后在该解释器环境安装依赖：

```bash
python -m pip install -r python_mcp_server/requirements.txt
```

运行 `test_ai_mcp.py` 前，在 PyCharm 的 Run Configuration 环境变量中设置 `DASHSCOPE_API_KEY`。
`DASHSCOPE_EMBEDDING_KEY` 可单独设置；不设置时脚本会提示输入，直接回车可复用 LLM API Key。
脚本会用当前 PyCharm 解释器启动 `python_mcp_server/server.py`，再进行工具发现、RAG 和大模型调用。

#### 手动测试

```bash
# 通过管道发送 JSON-RPC 请求
echo '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1.0"}},"id":"1"}' \
  | ./build/mcp_server/mcp_server -p ./build/mcp_server/plugins -l ./build/test_logs
```

#### Python MCP 客户端测试

```bash
cd mcp_server/test
pip install -r requirements.txt
python3 test-client-stdio.py configuration-stdio-linux.json
```

## 开发自定义插件

参见 [docs/mcp-plugin-development.md](docs/mcp-plugin-development.md)。

## RAG-MCP 详细指南

参见 [docs/rag-mcp-guide.md](docs/rag-mcp-guide.md)。

## 协议版本

- MCP Client: JSON-RPC 2.0
- MCP Server: v0.7.0

## License

MCP Server 部分基于 MIT License (Copyright (C) 2025 Giuseppe Mastrangelo)。
其余部分遵循原项目许可证。
