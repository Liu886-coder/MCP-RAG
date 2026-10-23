"""Python MCP server that mirrors the calculator and sleep demo tools."""

import ast
import datetime
import math
import operator
import time
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("mcp-python-server")

_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_MATH_FUNCTIONS = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "sqrt": math.sqrt,
    "abs": abs,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
    "round": round,
}

# _evaluate() / _calculate() 是计算器内部实现，不会作为独立 MCP 工具暴露
# 带 @mcp.tool() 的函数是客户端能调用的工具
def _evaluate(node):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        return node.value

    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > 100:
            raise ValueError("Exponent magnitude must be 100 or less")
        return _BINARY_OPERATORS[type(node.op)](left, right)

    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_evaluate(node.operand))

    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id in _MATH_FUNCTIONS and len(node.args) == 1
            and not node.keywords):
        return _MATH_FUNCTIONS[node.func.id](_evaluate(node.args[0]))

    raise ValueError("Expression contains an unsupported operation")


def _calculate(expression):
    if not expression or len(expression) > 256:
        raise ValueError("Expression must contain between 1 and 256 characters")
    tree = ast.parse(expression, mode="eval")
    result = _evaluate(tree.body)
    if not math.isfinite(result):
        raise ValueError("Result is not a finite number")
    return result

# 调用 _calculate()，再返回如 "2+3*4 = 14" 的文字结果
@mcp.tool()
def calculator(expression: str) -> str:
    """Evaluate a mathematical expression such as 2+3*4 or sqrt(16)."""
    result = _calculate(expression)
    return f"{expression} = {result:.15g}"

# 返回两数相加的结果
@mcp.tool()
def add(a: float, b: float) -> str:
    """Add two numbers."""
    return f"{a} + {b} = {a + b}"

# 返回 a - b
@mcp.tool()
def subtract(a: float, b: float) -> str:
    """Subtract b from a."""
    return f"{a} - {b} = {a - b}"

# 返回两数相乘。
@mcp.tool()
def multiply(a: float, b: float) -> str:
    """Multiply two numbers."""
    return f"{a} * {b} = {a * b}"

# 先检查 b 是否为零，再做除法
@mcp.tool()
def divide(a: float, b: float) -> str:
    """Divide a by b."""
    if b == 0:
        raise ValueError("Division by zero")
    return f"{a} / {b} = {a / b}"

# 计算 base 的 exponent 次方，也限制指数大小
@mcp.tool()
def power(base: float, exponent: float) -> str:
    """Raise base to exponent."""
    if abs(exponent) > 100:
        raise ValueError("Exponent magnitude must be 100 or less")
    return f"{base} ^ {exponent} = {base ** exponent}"

# 拒绝负数，然后计算平方根
@mcp.tool()
def sqrt(number: float) -> str:
    """Return the square root of a non-negative number."""
    if number < 0:
        raise ValueError("Cannot take the square root of a negative number")
    return f"sqrt({number}) = {math.sqrt(number)}"

# 限制 n 在 0 到 20 之间，再计算阶乘。
@mcp.tool()
def factorial(n: int) -> str:
    """Calculate the factorial of an integer from 0 through 20."""
    if n < 0 or n > 20:
        raise ValueError("n must be between 0 and 20")
    return f"{n}! = {math.factorial(n)}"

# 把毫秒换算成秒，调用 time.sleep() 暂停程序，然后返回等待了多久。
@mcp.tool()
def sleep(milliseconds: int) -> str:
    """Pause execution for the specified number of milliseconds (0-60000)."""
    if milliseconds < 0 or milliseconds > 60000:
        raise ValueError("milliseconds must be between 0 and 60000")
    time.sleep(milliseconds / 1000)
    return f"Waited for {milliseconds} milliseconds"


_WEATHER_CODES = {
    0: "晴",
    1: "大致晴朗",
    2: "局部多云",
    3: "阴",
    45: "有雾",
    48: "冻雾",
    51: "小毛毛雨",
    53: "中等毛毛雨",
    55: "强毛毛雨",
    56: "轻微冻毛毛雨",
    57: "强冻毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    66: "小冻雨",
    67: "强冻雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    77: "雪粒",
    80: "小阵雨",
    81: "中等阵雨",
    82: "强阵雨",
    85: "小阵雪",
    86: "强阵雪",
    95: "雷暴",
    96: "伴有小冰雹的雷暴",
    99: "伴有大冰雹的雷暴",
}


def _open_meteo_json(url: str) -> dict:
    for attempt in range(3):
        try:
            with urlopen(url, timeout=10) as response:
                return json.load(response)
        except HTTPError as exc:
            raise ValueError(f"Weather service returned HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            if attempt == 2:
                raise ValueError(
                    "Weather service connection failed after 3 attempts. "
                    "Check internet access or HTTPS_PROXY settings. "
                    f"Details: {exc}"
                ) from exc
            time.sleep(0.5 * (attempt + 1))
        except json.JSONDecodeError as exc:
            raise ValueError("Weather service returned invalid data") from exc


def _find_location(city: str) -> dict:
    city = city.strip()
    if not city:
        raise ValueError("City must not be empty")

    location_url = "https://geocoding-api.open-meteo.com/v1/search?" + urlencode({
        "name": city,
        "count": 1,
        "language": "zh",
        "format": "json",
    })
    locations = _open_meteo_json(location_url).get("results", [])
    if not locations:
        raise ValueError(f"Could not find city: {city}")
    return locations[0]


def _format_place(location: dict) -> str:
    return ", ".join(filter(None, [
        location.get("name"),
        location.get("admin1"),
        location.get("country"),
    ]))


@mcp.tool()
def get_weather(city: str) -> str:
    """Get the current weather for a city, for example Shanghai or Beijing."""
    location = _find_location(city)
    weather_url = "https://api.open-meteo.com/v1/forecast?" + urlencode({
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "current": (
            "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "is_day,precipitation,weather_code,wind_speed_10m"
        ),
        "timezone": "auto",
    })
    current = _open_meteo_json(weather_url).get("current")
    if not current:
        raise ValueError("Weather service returned no current conditions")

    place = _format_place(location)
    condition = _WEATHER_CODES.get(current.get("weather_code"), "未知天气")
    return (
        f"{place} 当前天气（当地时间 {current.get('time', '未知')}）："
        f"{condition}，气温 {current['temperature_2m']}°C，"
        f"体感 {current['apparent_temperature']}°C，"
        f"湿度 {current['relative_humidity_2m']}%，"
        f"风速 {current['wind_speed_10m']} km/h，"
        f"降水量 {current['precipitation']} mm。"
    )


@mcp.tool()
def get_weather_forecast(city: str, date: str) -> str:
    """Get the weather forecast for a city and date in YYYY-MM-DD format."""
    try:
        requested_date = datetime.date.fromisoformat(date)
    except (TypeError, ValueError) as exc:
        raise ValueError("date must use YYYY-MM-DD format") from exc

    today = datetime.date.today()
    if requested_date < today or requested_date > today + datetime.timedelta(days=15):
        raise ValueError("Forecast date must be today or within the next 15 days")

    location = _find_location(city)
    weather_url = "https://api.open-meteo.com/v1/forecast?" + urlencode({
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "daily": (
            "weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_sum,wind_speed_10m_max,sunrise,sunset"
        ),
        "start_date": requested_date.isoformat(),
        "end_date": requested_date.isoformat(),
        "timezone": "auto",
    })
    daily = _open_meteo_json(weather_url).get("daily")
    if not daily or not daily.get("time"):
        raise ValueError("Weather service returned no forecast for that date")

    condition = _WEATHER_CODES.get(daily["weather_code"][0], "未知天气")
    return (
        f"{_format_place(location)} {requested_date.isoformat()} 天气预报："
        f"{condition}，最高气温 {daily['temperature_2m_max'][0]}°C，"
        f"最低气温 {daily['temperature_2m_min'][0]}°C，"
        f"降水量 {daily['precipitation_sum'][0]} mm，"
        f"最大风速 {daily['wind_speed_10m_max'][0]} km/h，"
        f"日出 {daily['sunrise'][0]}，日落 {daily['sunset'][0]}。"
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
# @mcp.tool()相当于告诉 MCP：“这个 Python 函数，是一个可以被外部 Agent 调用的工具。”
