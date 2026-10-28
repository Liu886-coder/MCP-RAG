#!/usr/bin/env python3
"""批量评估 RAG 工具召回、LLM 选择和实际调用，不修改 test_ai_mcp.py。

运行：
    python test.py                         # RAG + LLM + MCP 实际执行
    python test.py --rag-only              # 仅测检索，不调用 LLM 或执行工具
    python test.py --top-k 5 --threshold 0.2
    python test.py --limit 3               # 先用少量问题试跑

密钥沿用 DASHSCOPE_API_KEY / DASHSCOPE_EMBEDDING_KEY；缺失时隐藏输入。
每题是一个单步任务，expected_tools 是可替代的正确工具，而非都要调用。
候选列表包含任意一个可接受工具即命中，统计 Hit@K（单步任务召回率）。
无检索结果时沿用主程序的全量回退，但回退不会计为 RAG 召回命中。
仅执行第一个 tool_call，与当前主程序一致；不再请求最终自然语言回答，
因为该步骤不影响工具召回/调用指标。默认会真实访问百炼和天气服务。
"""

import argparse
import datetime as dt
import getpass
import json
import math
import os
from pathlib import Path
import sys

from test_ai_mcp import MCPStdioClient, RAGToolIndex, call_llm, mcp_tools_to_openai_format


ROOT = Path(__file__).resolve().parent
SYSTEM_PROMPT = "你是一个智能助手，可以使用提供的工具来回答用户问题。当需要使用工具时，请调用对应的工具。"


def build_test_cases():
    """可在这里增加问题和标准答案；日期动态生成，避免天气用例过期。"""
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    cases = []
    # 简单算术允许 calculator 作为正确替代工具，避免把有效调用误判为错误。
    arithmetic = [
        ("calculator", "计算表达式 2+3*4 的结果", 14),
        ("calculator", "帮我计算 (12-4)/2", 4),
        ("add", "帮我算一下 23 加 19", 42),
        ("add", "-5 和 12 相加等于多少？", 7),
        ("subtract", "100 减去 37 是多少？", 63),
        ("subtract", "用 8 减去 15", -7),
        ("multiply", "计算 7 乘以 8", 56),
        ("multiply", "2.5 乘 4 等于多少？", 10),
        ("divide", "计算 81 除以 9", 9),
        ("divide", "7 除以 2 是多少？", 3.5),
        ("power", "求 2 的 10 次方", 1024),
        ("power", "3 的 4 次方是多少？", 81),
        ("sqrt", "求 144 的平方根", 12),
        ("sqrt", "2.25 的平方根是多少？", 1.5),
        ("factorial", "计算 5 的阶乘", 120),
        ("factorial", "0 的阶乘是多少？", 1),
    ]
    for tool, query, value in arithmetic:
        alternatives = [tool]
        # 当前 calculator 支持 sqrt、幂运算，但不支持 factorial。
        if tool not in ("calculator", "factorial"):
            alternatives.append("calculator")
        cases.append(dict(query=query, expected_tools=alternatives, expected_value=value))
    cases.extend([
        dict(query="请暂停 100 毫秒", expected_tools=["sleep"], milliseconds=100),
        dict(query="等待 200 毫秒再继续", expected_tools=["sleep"], milliseconds=200),
        dict(query="看一下上海现在的天气", expected_tools=["get_weather"],
             cities=["上海", "上海市", "shanghai"]),
        dict(query="北京当前气温和天气怎么样？", expected_tools=["get_weather"],
             cities=["北京", "北京市", "beijing", "peking"]),
        dict(query=f"查询上海 {tomorrow} 的天气预报", expected_tools=["get_weather_forecast"],
             cities=["上海", "上海市", "shanghai"], date=tomorrow),
        dict(query=f"北京 {tomorrow} 的最高温和最低温是多少？", expected_tools=["get_weather_forecast"],
             cities=["北京", "北京市", "beijing", "peking"], date=tomorrow),
    ])
    return [dict(id=i, **case) for i, case in enumerate(cases, 1)]


def check_output(case, arguments, text):
    """工具未报错仍不一定完成了任务：校验数值、等待参数或天气地点/日期。"""
    if "expected_value" in case:
        try:
            actual = float(text.rsplit("=", 1)[1].strip())
            ok = math.isclose(actual, case["expected_value"], rel_tol=1e-9, abs_tol=1e-9)
        except (ValueError, IndexError):
            ok = False
        return ok, "数值符合预期" if ok else f"结果应为 {case['expected_value']}"
    if "milliseconds" in case:
        ok = (arguments.get("milliseconds") == case["milliseconds"]
              and text.strip() == f"Waited for {case['milliseconds']} milliseconds")
        return ok, "等待时间符合预期" if ok else "等待参数或结果不符合预期"
    city = str(arguments.get("city", "")).strip().casefold()
    ok = city in case["cities"] and bool(text.strip())
    if "date" in case:
        ok = ok and arguments.get("date") == case["date"] and case["date"] in text
    return ok, "地点/日期参数及非空返回已核对（未独立核验气象数据）" if ok else "地点、日期或返回结果不符合预期"


def evaluate_case(case, rag_index, mcp, all_tools, api_key, args):
    result = dict(case=case, retrieved=[], rag_hit=False, fallback=False,
                  selected_tool=None, arguments=None, selection_correct=False,
                  execution_ok=False, correct_call=False, error=None)
    stage = "retrieval"
    try:
        retrieved = rag_index.retrieve(case["query"], top_k=args.top_k, threshold=args.threshold)
        result["retrieved"] = [dict(name=t["name"], score=score) for t, score in retrieved]
        names = {t["name"] for t, _ in retrieved}
        result["rag_hit"] = bool(names.intersection(case["expected_tools"]))
        if args.rag_only:
            return result

        # 先记录原始检索结果，再回退，防止把全量工具误记为召回成功。
        result["fallback"] = not names
        candidates = [t for t in all_tools if t["function"]["name"] in names] if names else all_tools
        candidate_names = {t["function"]["name"] for t in candidates}
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": case["query"]}]
        stage = "llm"
        response = call_llm(api_key, args.model, messages, candidates)
        choices = response.get("choices") or []
        if not choices or not isinstance(choices[0].get("message"), dict):
            raise ValueError("LLM 未返回有效 message")
        message = choices[0]["message"]
        calls = message.get("tool_calls") or []
        result["tool_call_count"] = len(calls)
        if not calls:
            result["error"] = "LLM 直接回答，未调用工具；本测试集每题均要求调用工具"
            result["error_stage"] = "selection"
            return result

        function = calls[0].get("function", {})
        name = function.get("name", "")
        result["selected_tool"] = name
        result["selection_correct"] = name in case["expected_tools"] and name in candidate_names
        stage = "arguments"
        arguments = json.loads(function.get("arguments", "{}"))
        if not isinstance(arguments, dict):
            raise ValueError("工具参数必须是 JSON 对象")
        result["arguments"] = arguments
        if name not in candidate_names:
            raise ValueError(f"LLM 选择了未提供的工具：{name}")

        stage = "execution"
        response = mcp.call_tool(name, arguments)
        if response is None:
            raise RuntimeError("MCP 工具调用无响应")
        text = "".join(c.get("text", "") for c in response.get("content", [])
                       if c.get("type") == "text")
        result["tool_output"] = text
        if response.get("isError", False):
            raise RuntimeError(f"工具返回错误：{text}")
        result["execution_ok"] = True
        stage = "validation"
        valid, note = check_output(case, arguments, text)
        result["validation"] = note
        result["correct_call"] = result["selection_correct"] and valid
        if not result["selection_correct"]:
            result["error"] = "LLM 未选择标注的可接受工具"
            result["error_stage"] = "selection"
        elif not valid:
            result["error"] = note
            result["error_stage"] = "validation"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["error_stage"] = stage
    return result


def summarize(results, rag_only):
    total = len(results)
    hits = sum(r["rag_hit"] for r in results)
    metrics = dict(total=total, rag_hits=hits, rag_hit_rate=hits / total if total else None,
                   retrieval_errors=sum(r.get("error_stage") == "retrieval" for r in results))
    if not rag_only:
        selected = sum(r["selection_correct"] for r in results)
        correct = sum(r["correct_call"] for r in results)
        metrics.update(selection_correct=selected, selection_accuracy=selected / total if total else None,
                       correct_calls=correct, correct_call_rate=correct / total if total else None,
                       fallback_cases=sum(r["fallback"] for r in results),
                       execution_errors=sum(r.get("error_stage") == "execution" for r in results),
                       llm_errors=sum(r.get("error_stage") == "llm" for r in results))
    return metrics


def run_evaluation(args, api_key, embedding_key):
    cases = build_test_cases()
    if args.limit:
        cases = cases[:args.limit]
    mcp = MCPStdioClient(str(ROOT / "python_mcp_server" / "server.py"), str(ROOT / "build" / "test_logs"))
    results = []
    try:
        mcp.start()
        initialized = mcp.initialize()
        if not initialized or "result" not in initialized:
            raise RuntimeError("MCP 初始化失败，请确认当前 Python 环境已安装 python_mcp_server/requirements.txt")
        tools = mcp.list_tools()
        if not tools:
            raise RuntimeError("MCP 工具列表为空")
        available = {t["name"] for t in tools}
        for case in cases:
            if not available.intersection(case["expected_tools"]):
                raise ValueError(f"用例 {case['id']} 的标注工具在服务端均不存在")
        index = RAGToolIndex(embedding_key)
        print(f"正在为 {len(tools)} 个工具建立向量索引……", flush=True)
        if index.build_index(tools) != len(tools):
            raise RuntimeError("Embedding 返回的向量数量与工具数量不一致")
        if any(len(v) != index.dimension or not any(v) or not all(math.isfinite(x) for x in v)
               for v in index.vectors):
            raise RuntimeError("工具向量存在空向量、维度不一致或非有限数值")
        all_tools = mcp_tools_to_openai_format(tools)
        print(f"开始测试 {len(cases)} 题，top_k={args.top_k}，threshold={args.threshold}", flush=True)
        print("正确工具列表中的多个名称表示可替代；每题只执行第一个 tool_call。")
        for case in cases:
            print(f"\n[{case['id']}/{len(cases)}] {case['query']}", flush=True)
            print(f"  可接受工具：{', '.join(case['expected_tools'])}")
            result = evaluate_case(case, index, mcp, all_tools, api_key, args)
            results.append(result)
            candidates = ", ".join(f"{t['name']}({t['score']:.4f})" for t in result["retrieved"])
            print(f"  RAG：{candidates or '无命中'}；召回{'成功' if result['rag_hit'] else '失败'}")
            if not args.rag_only:
                print(f"  全量回退：{'是' if result['fallback'] else '否'}；LLM 选择：{result['selected_tool'] or '无'}")
                print(f"  参数：{json.dumps(result['arguments'], ensure_ascii=False)}")
                print(f"  正确调用：{'成功' if result['correct_call'] else '失败'}")
            if result["error"]:
                print(f"  原因 [{result['error_stage']}]：{result['error']}")
    finally:
        mcp.stop()

    metrics = summarize(results, args.rag_only)
    total = metrics["total"]
    print(f"\n{'=' * 60}\n测试汇总（所有测试题均计入分母，异常不静默剔除）")
    print(f"RAG 候选命中率 Hit@{args.top_k}（单步任务召回率）："
          f"{metrics['rag_hits']}/{total} = {metrics['rag_hit_rate']:.2%}")
    print(f"检索异常：{metrics['retrieval_errors']} 题")
    if not args.rag_only:
        print(f"LLM 工具选择正确率：{metrics['selection_correct']}/{total} = {metrics['selection_accuracy']:.2%}")
        print(f"正确工具调用成功率：{metrics['correct_calls']}/{total} = {metrics['correct_call_rate']:.2%}")
        print(f"全量回退：{metrics['fallback_cases']} 题；LLM 异常：{metrics['llm_errors']} 题；"
              f"执行异常：{metrics['execution_errors']} 题")
    print("天气仅核对工具名、地点/日期参数和非空返回，不独立判断气象数据真实性。")
    report = dict(created_at=dt.datetime.now().astimezone().isoformat(),
                  config=dict(model=args.model, top_k=args.top_k, threshold=args.threshold,
                              rag_only=args.rag_only, embedding_model=index.model),
                  metric_definition="每题为单步任务；RAG 候选命中任一可接受工具即成功；全量回退不计召回命中。",
                  metrics=metrics, results=results)
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"逐题报告：{output}")
    target = metrics["rag_hits"] if args.rag_only else metrics["correct_calls"]
    return 0 if target == total else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--top-k", type=int, default=3, help="最多召回工具数，默认 3")
    parser.add_argument("--threshold", type=float, default=0.2, help="余弦相似度阈值，默认 0.2")
    parser.add_argument("--model", default=os.environ.get("DASHSCOPE_MODEL") or "qwen-plus")
    parser.add_argument("--rag-only", action="store_true", help="只测 RAG，不调用 LLM、不执行工具")
    parser.add_argument("--limit", type=int, help="只运行前 N 题")
    parser.add_argument("--output", type=Path, default=ROOT / "build" / "tool_recall_report.json")
    args = parser.parse_args()
    if args.top_k < 1 or not math.isfinite(args.threshold) or not -1 <= args.threshold <= 1:
        parser.error("top-k 必须为正整数，threshold 必须是 [-1, 1] 内的有限数")
    if args.limit is not None and args.limit < 1:
        parser.error("limit 必须为正整数")
    try:
        api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        embedding_key = os.environ.get("DASHSCOPE_EMBEDDING_KEY", "").strip()
        if not args.rag_only and not api_key:
            api_key = getpass.getpass("请输入百炼 API Key（输入不显示）：").strip()
        if not args.rag_only and not api_key:
            raise ValueError("LLM API Key 不能为空")
        embedding_key = embedding_key or api_key
        if not embedding_key:
            embedding_key = getpass.getpass("请输入百炼 Embedding Key（输入不显示）：").strip()
        if not embedding_key:
            raise ValueError("Embedding Key 不能为空")
        return run_evaluation(args, api_key, embedding_key)
    except KeyboardInterrupt:
        print("\n测试已中断。")
        return 130
    except Exception as exc:
        print(f"测试未完成：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
