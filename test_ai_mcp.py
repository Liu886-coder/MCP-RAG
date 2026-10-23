#!/usr/bin/env python3
"""
AI + MCP 集成测试

使用阿里百炼 (DashScope) AI 模型作为 Agent，通过 MCP 协议调用工具，
验证完整的 AI Agent → RAG 工具检索 → MCP Server → Plugin 工具调用链路。

流程:
  1. 启动 MCP Server 子进程 (STDIO 传输)
  2. 初始化 MCP 连接，获取可用工具列表
  3. 使用 Embedding 对所有工具建立向量索引 (RAG)
  4. 用户输入查询 → Embedding 向量化 → 余弦相似度检索相关工具
  5. 仅将 RAG 筛选出的工具发送给 LLM (而非全部工具)
  6. LLM 返回 tool_calls → 通过 MCP 执行
  7. 将工具结果回传 LLM，获取最终回答
"""

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

# ============================================================================
# 颜色输出
# ============================================================================
RED = '\033[0;31m'
GREEN = '\033[0;32m'
YELLOW = '\033[1;33m'
CYAN = '\033[0;36m'
BOLD = '\033[1m'
NC = '\033[0m'

def color(c, text):
    return f"{c}{text}{NC}"

def log_step(n, title):
    print(f"\n{BOLD}{'='*60}{NC}")
    print(f"  {BOLD}步骤 {n}: {title}{NC}")
    print(f"{BOLD}{'='*60}{NC}")

def log_ok(msg):
    print(f"  {color(GREEN, '[OK]')} {msg}")

def log_fail(msg):
    print(f"  {color(RED, '[FAIL]')} {msg}")

def log_info(msg):
    print(f"  {color(CYAN, '[INFO]')} {msg}")

# ============================================================================
# MCP 通信
# ============================================================================
# 相当于是再告诉 Python 怎么和 MCP Server 说话
# 这块也就是相当于MCP_Client, 这块是一个python的实现。C++的实现在目录下mcp_client
class MCPStdioClient:
    """通过 STDIO 与 MCP Server 通信的简易客户端"""

    def __init__(self, server_path, logs_path):
        self.server_path = server_path   #  也就是工具脚本server.py，这个是自定义的python模式
        self.logs_path = logs_path       # 保存日志的
        self.proc = None
        self.request_id = 0

    # mcp.start() 通过 subprocess.Popen 启动一个独立的 Python Server 子进程
    # stdin stdout 也就是STDIO 通信;Standard Input / Output，标准输入 / 标准输出
    def start(self):
        os.makedirs(self.logs_path, exist_ok=True)
        self.proc = subprocess.Popen(
            [sys.executable, self.server_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,  # MCP 消息走 stdout；错误信息保留在控制台便于排查
            text=True,
            encoding="utf-8",
            cwd=os.path.dirname(self.server_path),
        )

    def stop(self):
        if self.proc:
            try:
                self.proc.stdin.close()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()
            self.proc = None

    def _is_alive(self):
        return self.proc and self.proc.poll() is None

    def send(self, method, params=None):
        if not self._is_alive():
            rc = self.proc.returncode if self.proc else "N/A"
            print(f"  {YELLOW}[DEBUG] MCP 服务器已退出 (returncode={rc}){NC}",
                  file=sys.stderr)
            return None

        self.request_id += 1
        req_id = str(self.request_id)
        req = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
            "id": req_id,
        }
        line = json.dumps(req, ensure_ascii=False)
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            print(f"  {YELLOW}[DEBUG] 写入 stdin 失败: {e}{NC}", file=sys.stderr)
            return None

        # 读取响应，跳过服务器推送的 notification 消息
        # notification 没有 "id" 字段，而 response 有 "id" 匹配请求
        max_lines = 50  # 防止无限循环
        for i in range(max_lines):
            try:
                raw_line = self.proc.stdout.readline()  # 不要先 strip
            except Exception as e:
                print(f"  {YELLOW}[DEBUG] readline 异常: {e}{NC}", file=sys.stderr)
                return None

            # readline() 返回 "" 仅在 EOF 时；返回 "\n" 表示空行
            if not raw_line:
                rc = self.proc.poll()
                print(f"  {YELLOW}[DEBUG] stdout EOF (进程状态={rc}){NC}",
                      file=sys.stderr)
                return None

            resp_line = raw_line.strip()
            if not resp_line:
                # 空行，跳过继续读（不是 EOF）
                continue

            try:
                msg = json.loads(resp_line)
            except json.JSONDecodeError:
                # 非 JSON 行（如日志泄露到 stdout），跳过
                continue

            # 检查是否是我们请求的响应 (有 "id" 字段且匹配)
            if "id" in msg and str(msg["id"]) == req_id:
                return msg

            # 否则是 notification 或其他请求的响应，跳过继续读

        print(f"  {YELLOW}[DEBUG] 超过 {max_lines} 行未找到匹配响应 "
              f"(req_id={req_id}){NC}", file=sys.stderr)
        return None

    def initialize(self):
        response = self.send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ai-test-client", "version": "1.0.0"},
        })
        if response and "result" in response:
            self.send_notification("notifications/initialized")
        return response

    def send_notification(self, method, params=None):
        if not self._is_alive():
            return
        notification = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            notification["params"] = params
        try:
            self.proc.stdin.write(json.dumps(notification, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as e:
            print(f"  {YELLOW}[DEBUG] 发送通知失败: {e}{NC}", file=sys.stderr)

    # Client 问 Server：“你有什么工具？”
    def list_tools(self):
        resp = self.send("tools/list")
        if resp and "result" in resp and "tools" in resp["result"]:
            return resp["result"]["tools"]
        return []
    # 告诉 Server：“帮我调用这个工具。”
    def call_tool(self, name, arguments):
        resp = self.send("tools/call", {"name": name, "arguments": arguments})
        if resp and "result" in resp:
            return resp["result"]
        if resp and "error" in resp:
            # JSON-RPC error → 转为统一格式
            return {"isError": True, "content": [
                {"type": "text", "text": str(resp["error"].get("message", resp["error"]))}
            ]}
        return None

# ============================================================================
# DashScope API
# ============================================================================
# 代码配置了两个 API 地址，Chat Completions：发送对话，让大模型回答或选择工具  Embedding：把文本转换成数字向量，供 RAG 比较文本语义。
DASHSCOPE_CHAT_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
DASHSCOPE_EMBED_URL = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"

# call_llm()把模型名、对话 messages 和可选工具列表组成请求，通过 HTTP 发给大模型
def call_llm(api_key, model, messages, tools=None):
    """调用百炼 LLM (OpenAI 兼容接口)"""
    body = {
        "model": model,
        "messages": messages,
    }
    if tools:
        body["tools"] = tools

    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        DASHSCOPE_CHAT_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))

#
def call_embedding(api_key, text, model="text-embedding-v2"):
    """调用百炼 Embedding API (单文本)"""
    return call_embedding_batch(api_key, [text], model)[0]


def call_embedding_batch(api_key, texts, model="text-embedding-v2"):
    """调用百炼 Embedding API (批量)"""
    body = {
        "model": model,
        "input": {"texts": texts},
        "parameters": {"text_type": "query"},
    }
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        DASHSCOPE_EMBED_URL,
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read().decode("utf-8"))
        embeddings = result.get("output", {}).get("embeddings", [])
        # DashScope 返回的 embeddings 按 text_index 排序
        embeddings.sort(key=lambda e: e.get("text_index", 0))
        return [e.get("embedding", []) for e in embeddings]

# RAG：先帮模型缩小候选工具范围
def cosine_similarity(a, b):
    """计算余弦相似度"""
    import math
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)

# 把工具名称、描述和参数名组成文本，调用 Embedding 服务生成工具向量；再把你的问题向量化，按相似度筛选候选工具。
class RAGToolIndex:
    """RAG 工具向量索引 - 模拟 C++ ToolRetriever 的 Python 实现"""

    def __init__(self, embedding_key, model="text-embedding-v2"):
        self.embedding_key = embedding_key
        self.model = model
        self.tools = []        # MCP 工具定义列表
        self.vectors = []      # 对应的向量
        self.dimension = 0

    # 把每个工具整理成一段文本，例如工具名、描述和参数名；调用 Embedding API 把这些文本变成向量。
    def build_index(self, mcp_tools):
        """为所有工具生成向量并建立索引"""
        self.tools = mcp_tools
        # 构建工具文本表示 (与 C++ ToolRetriever::buildToolText 逻辑一致)
        texts = []
        for t in mcp_tools:
            text = f"{t['name']}: {t.get('description', '')}"
            schema = t.get("inputSchema", {})
            props = schema.get("properties", {})
            if props:
                params = ", ".join(props.keys())
                text += f" (参数: {params})"
            texts.append(text)
        self.vectors = call_embedding_batch(self.embedding_key, texts, self.model)
        if self.vectors:
            self.dimension = len(self.vectors[0])
        return len(self.vectors)

    # 把用户问题也转成向量，与每个工具向量计算相似度；去掉低于阈值的结果，按分数从高到低排序，最多取 top_k 个
    def retrieve(self, query, top_k=3, threshold=0.3):
        """
        根据查询检索相关工具。
        返回 [(tool, score), ...] 按相关性降序排列。
        """
        query_vec = call_embedding(self.embedding_key, query, self.model)
        scored = []
        for tool, vec in zip(self.tools, self.vectors):
            score = cosine_similarity(query_vec, vec)
            if score >= threshold:
                scored.append((tool, score))
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:top_k]


def mcp_tools_to_openai_format(mcp_tools):
    """将 MCP 工具定义转换为 OpenAI function calling 格式"""
    tools = []
    for t in mcp_tools:
        schema = t.get("inputSchema", {})
        tools.append({
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": schema,
            },
        })
    return tools

# ============================================================================
# 测试用例
# ============================================================================

def run_tests(api_key, embedding_key, llm_model):
    script_dir = os.path.dirname(os.path.abspath(__file__))
    server_path = os.path.join(script_dir, "python_mcp_server", "server.py")   # 修改调用，走python_mcp_server里的server.py；server.py主要装着一些工具列表
    logs_path = os.path.join(script_dir, "build", "test_logs")

    if not os.path.isfile(server_path):
        log_fail(f"未找到 Python MCP Server: {server_path}")
        print("请确认 python_mcp_server/server.py 文件存在，并在当前 Python 环境安装 mcp 依赖。")
        return False

    results = []

    def record(name, ok, detail=""):
        results.append((name, ok, detail))
        if ok:
            log_ok(name + (f" - {detail}" if detail else ""))
        else:
            log_fail(name + (f" - {detail}" if detail else ""))

    # ------------------------------------------------------------------
    # 步骤 1: 启动 MCP Server 并初始化
    # ------------------------------------------------------------------
    log_step(1, "启动 MCP Server")

    mcp = MCPStdioClient(server_path, logs_path)  # 调取该类
    try:
        mcp.start()
    except Exception as e:
        record("启动 MCP Server", False, str(e))
        return False

    init_resp = mcp.initialize()
    if init_resp and "result" in init_resp:
        server_info = init_resp["result"].get("serverInfo", {})
        record("MCP 初始化", True,
               f"服务器: {server_info.get('name')} v{server_info.get('version')}")
    else:
        record("MCP 初始化", False, "未收到有效响应")
        mcp.stop()
        return False

    # ------------------------------------------------------------------
    # 步骤 2: 获取工具列表
    # ------------------------------------------------------------------
    log_step(2, "获取 MCP 工具列表")

    mcp_tools = mcp.list_tools() # 在MCPStdioClient类中
    if mcp_tools:
        record("获取工具列表", True, f"共 {len(mcp_tools)} 个工具")
        for t in mcp_tools:
            log_info(f"  {t['name']}: {t.get('description', 'N/A')}")
    else:
        record("获取工具列表", False, "工具列表为空")
        mcp.stop()
        return False

    # ------------------------------------------------------------------
    # 步骤 3: 构建 RAG 工具向量索引
    # ------------------------------------------------------------------
    log_step(3, "构建 RAG 工具向量索引 (Embedding)")

    rag_index = RAGToolIndex(embedding_key)
    rag_ok = False

    try:
        count = rag_index.build_index(mcp_tools)   # 创建索引
        if count > 0:
            record("RAG 索引构建", True,
                   f"已索引 {count} 个工具, 向量维度: {rag_index.dimension}")
            rag_ok = True
            for t, v in zip(mcp_tools, rag_index.vectors):
                log_info(f"  已向量化: {t['name']} (向量非零: {sum(1 for x in v if x != 0)})")
        else:
            record("RAG 索引构建", False, "未生成任何向量")
    except Exception as e:
        record("RAG 索引构建", False, str(e))

    # ------------------------------------------------------------------
    # 步骤 4: AI Agent 交互式工具调用 (RAG + LLM + MCP)
    # ------------------------------------------------------------------
    log_step(4, "AI Agent 交互式工具调用 (RAG + LLM + MCP)")

    all_openai_tools = mcp_tools_to_openai_format(mcp_tools)

    print()
    print(f"  {BOLD}可用工具:{NC}")
    for t in mcp_tools:
        print(f"    - {t['name']}: {t.get('description', '')}")
    print()
    if rag_ok:
        print(f"  {color(GREEN, 'RAG 已启用')}: 查询会先通过向量相似度筛选相关工具，再发给 LLM。")
    else:
        print(f"  {color(YELLOW, 'RAG 未启用')}: 将所有工具直接发给 LLM。")
    print(f"  输入问题让 AI 选择并调用工具，输入 {BOLD}q{NC} 或空行结束。")
    print()

    round_num = 0
    while True:
        try:
            query = input(f"  {color(CYAN, '你的问题')}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not query or query.lower() == "q":
            break

        round_num += 1
        test_name = f"AI 工具调用 #{round_num}: {query[:30]}"

        try:
            # ---- 4a. RAG 检索相关工具 ----
            tools_for_llm = all_openai_tools  # 默认: 全部工具   tools_for_llm 是 Python 列表，内容是工具定义

            if rag_ok:
                retrieved = rag_index.retrieve(query, top_k=3, threshold=0.2) # RAG 把用户问题和工具描述做向量相似度比较，最多选出 3 个分数不低于 0.2 的工具
                if retrieved:
                    log_info(f"RAG 检索结果 (相似度 >= 0.2):")
                    for t, score in retrieved:
                        log_info(f"  {score:.4f}  {t['name']}: {t.get('description', '')[:50]}")
                    # 只把 RAG 筛选出的工具发给 LLM
                    retrieved_names = {t["name"] for t, _ in retrieved}
                    tools_for_llm = [t for t in all_openai_tools
                                     if t["function"]["name"] in retrieved_names]
                    log_info(f"发送给 LLM 的工具: {[t['function']['name'] for t in tools_for_llm]} "   # 如果筛到了工具，代码只把这些候选工具发给 LLM。
                             f"(RAG 筛选 {len(tools_for_llm)}/{len(all_openai_tools)})")
                else:
                    log_info(f"RAG 未找到相关工具 (阈值 0.2), 使用全部 {len(all_openai_tools)} 个工具")

            # ---- 4b. 调用 LLM，让它选择工具 ----
            # messages 是对话消息列表，system 是给模型的行为指引，user 是你的问题。
            messages = [
                {"role": "system", "content": "你是一个智能助手，可以使用提供的工具来回答用户问题。当需要使用工具时，请调用对应的工具。"},
                {"role": "user", "content": query},
            ]
            llm_resp = call_llm(api_key, llm_model, messages, tools_for_llm)

            choice = llm_resp.get("choices", [{}])[0]
            message = choice.get("message", {})
            tool_calls = message.get("tool_calls", [])

            if not tool_calls:
                # LLM 没有选择工具，直接回答
                content = message.get("content", "")
                log_info(f"AI 直接回答 (未调用工具): {content[:200]}")
                record(test_name, True, "LLM 直接回答")
                print()
                continue

            # ---- 4c. 通过 MCP 执行 LLM 选择的工具 ----
            tool_call = tool_calls[0]
            fn = tool_call.get("function", {})
            called_name = fn.get("name", "")
            called_args = json.loads(fn.get("arguments", "{}"))

            log_info(f"LLM 选择工具: {called_name}({json.dumps(called_args, ensure_ascii=False)})")

            mcp_result = mcp.call_tool(called_name, called_args)   # 脚本通过 MCP 的 tools/call 请求调用工具，拿到天气结果后再发给 LLM，让它组织最终回答

            if mcp_result is None:
                record(test_name, False, "MCP 工具调用无响应")
                print()
                continue

            is_error = mcp_result.get("isError", False)
            content_list = mcp_result.get("content", [])
            tool_output = ""
            for c in content_list:
                if c.get("type") == "text":
                    tool_output += c.get("text", "")

            log_info(f"MCP 工具结果: {tool_output[:200]}")

            if is_error:
                record(test_name, False, f"工具返回错误: {tool_output}")
                print()
                continue

            # ---- 4d. 将工具结果回传 LLM，获取最终回答 ----
            messages.append(message)  # assistant message with tool_calls
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.get("id", ""),
                "content": tool_output,
            })

            final_resp = call_llm(api_key, llm_model, messages)
            final_msg = final_resp.get("choices", [{}])[0].get("message", {}).get("content", "")

            log_info(f"AI 最终回答: {final_msg[:300]}")
            record(test_name, True, f"工具: {called_name}")

        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace") if hasattr(e, 'read') else ""
            record(test_name, False, f"API 错误 {e.code}: {body[:100]}")
        except Exception as e:
            record(test_name, False, str(e))

        print()

    # ------------------------------------------------------------------
    # 步骤 5: 直接工具调用验证
    # ------------------------------------------------------------------
    log_step(5, "直接 MCP 工具调用验证")

    # 检查服务器是否还活着，如果已退出则重启
    if not mcp._is_alive():
        rc = mcp.proc.returncode if mcp.proc else "N/A"
        log_info(f"MCP 服务器已退出 (returncode={rc})，正在重启...")
        mcp.stop()
        mcp.start()
        init_resp = mcp.initialize()
        if init_resp and "result" in init_resp:
            log_ok("MCP 服务器已重启并重新初始化")
        else:
            log_fail("重启 MCP 服务器失败，后续测试将跳过")
            record("MCP 服务器重启", False, "无法重新初始化")
    else:
        log_info("MCP 服务器进程正常运行中")

    # 5a. sleep 工具
    try:
        result = mcp.call_tool("sleep", {"milliseconds": 200})
        if result and not result.get("isError", False):
            record("调用 sleep 工具", True, "200ms")
        else:
            record("调用 sleep 工具", False, str(result))
    except Exception as e:
        record("调用 sleep 工具", False, str(e))

    # 5b. calculator 工具
    try:
        result = mcp.call_tool("calculator", {"expression": "2+3*4"})
        content_list = result.get("content", []) if result else []
        text = "".join(c.get("text", "") for c in content_list if c.get("type") == "text")
        if result and not result.get("isError", False):
            record("调用 calculator 工具", True, f"2+3*4 = {text}")
        else:
            record("调用 calculator 工具", False, text)
    except Exception as e:
        record("调用 calculator 工具", False, str(e))

    # 5c. 不存在的工具
    try:
        result = mcp.call_tool("no_such_tool", {})
        if result and result.get("isError", True):
            record("错误处理 (不存在的工具)", True)
        else:
            record("错误处理 (不存在的工具)", False, "未正确返回错误")
    except Exception as e:
        record("错误处理 (不存在的工具)", False, str(e))

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------
    mcp.stop()

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------
    print(f"\n{BOLD}{'='*60}{NC}")
    print(f"  {BOLD}测试汇总{NC}")
    print(f"{BOLD}{'='*60}{NC}")

    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total = len(results)

    print(f"  总计: {total}  {color(GREEN, f'通过: {passed}')}  {color(RED, f'失败: {failed}')}")
    print()
    for name, ok, detail in results:
        status = color(GREEN, "[PASS]") if ok else color(RED, "[FAIL]")
        suffix = f": {detail}" if detail and not ok else ""
        print(f"  {status} {name}{suffix}")

    print()
    if failed == 0:
        print(color(GREEN, "所有测试通过!"))
    else:
        print(color(RED, f"有 {failed} 个测试失败"))

    return failed == 0


# ============================================================================
# 入口
# ============================================================================

def main():
    print(f"{BOLD}{'='*60}{NC}")
    print(f"  {BOLD}AI + MCP 集成测试{NC}")
    print(f"  使用阿里百炼 (DashScope) AI 模型测试 MCP 完整链路")
    print(f"{BOLD}{'='*60}{NC}")
    print()

    # 读取 API Key
    api_key = os.environ.get("DASHSCOPE_API_KEY", "")
    embedding_key = os.environ.get("DASHSCOPE_EMBEDDING_KEY", "")

    if not api_key:
        api_key = input("请输入百炼 API Key (用于 LLM 模型): ").strip()
    if not api_key:
        print(color(RED, "错误: API Key 不能为空"))
        sys.exit(1)

    if not embedding_key:
        embedding_key = input("请输入百炼 Embedding Key (用于向量化，直接回车则与 API Key 相同): ").strip()
    if not embedding_key:
        embedding_key = api_key

    # 选择模型
    llm_model = os.environ.get("DASHSCOPE_MODEL", "")
    if not llm_model:
        print()
        print("可用模型: 1) qwen-plus  2) qwen-turbo  3) qwen-max")
        choice = input("选择 LLM 模型 [默认 1]: ").strip()
        model_map = {"1": "qwen-plus", "2": "qwen-turbo", "3": "qwen-max", "": "qwen-plus"}
        llm_model = model_map.get(choice, choice)

    print()
    log_info(f"LLM 模型: {llm_model}")
    log_info(f"API Key: {api_key[:8]}...{api_key[-4:]}")
    log_info(f"Embedding Key: {embedding_key[:8]}...{embedding_key[-4:]}")

    success = run_tests(api_key, embedding_key, llm_model)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
