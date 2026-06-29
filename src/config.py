"""配置加载

从环境变量和文件中读取配置。Cookie 单独放在 config/cookies.txt 方便更新。
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"

COOKIE_FILE = Path(os.environ.get("ZHISHU_COOKIE_FILE", CONFIG_DIR / "cookies.txt"))
DB_PATH = Path(os.environ.get("ZHISHU_DB_PATH", DATA_DIR / "zhishu.db"))
LOG_DIR = Path(os.environ.get("ZHISHU_LOG_DIR", PROJECT_ROOT / "logs"))

# API 访问 Token（防止公网被随便调用）
API_TOKEN = os.environ.get("ZHISHU_API_TOKEN", "")

# 监听地址和端口
API_HOST = os.environ.get("ZHISHU_API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("ZHISHU_API_PORT", "8000"))

# 默认查询天数
DEFAULT_DAYS = int(os.environ.get("ZHISHU_DEFAULT_DAYS", "30"))

# 可选的 HTTP/SOCKS5 代理（绕开服务器 IP 被百度风控）
# 格式： http://user:pass@host:port  或  socks5://user:pass@host:port
HTTP_PROXY = os.environ.get("ZHISHU_HTTP_PROXY", "").strip()


def load_cookie() -> str:
    """读取 cookies.txt 中的 Cookie 字符串。

    支持两种格式：
    1. 单行原始 Cookie 字符串（推荐，从浏览器DevTools直接复制）
    2. 多行 key=value 形式，会自动合并
    """
    if not COOKIE_FILE.exists():
        raise FileNotFoundError(
            f"Cookie 文件不存在: {COOKIE_FILE}\n"
            "请按以下步骤创建：\n"
            "  1. 浏览器登录 https://index.baidu.com\n"
            "  2. F12 → Network → 刷新页面 → 任选一个请求 → 复制 Cookie 头\n"
            f"  3. echo '<cookie字符串>' > {COOKIE_FILE}"
        )

    text = COOKIE_FILE.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"Cookie 文件为空: {COOKIE_FILE}")

    # 过滤掉注释行
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not lines:
        raise ValueError(f"Cookie 文件没有有效内容: {COOKIE_FILE}")

    # 如果是多行 key=value 形式则合并
    if all("=" in line and ";" not in line for line in lines):
        cookie = "; ".join(lines)
    else:
        # 否则取第一行作为完整 Cookie
        cookie = lines[0]

    # 真实 Baidu Cookie 一般 2000+ 字符；过短大概率是占位符或没贴完整
    if len(cookie) < 100:
        raise ValueError(
            f"Cookie 长度仅 {len(cookie)} 字符，太短了（真实 Cookie 一般 2000+ 字符）。"
            f"这可能是占位符示例，请把浏览器里完整的 Cookie 写入 {COOKIE_FILE}"
        )

    # HTTP Header 必须 ASCII；占位符里的中文括号会导致 UnicodeEncodeError
    try:
        cookie.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError(
            f"Cookie 含有非 ASCII 字符（中文/中文标点），HTTP 头不允许。"
            f"这通常是因为粘错了示例占位符。请重新从浏览器 DevTools 复制完整 Cookie。"
        )

    return cookie


def save_cookie(cookie: str) -> None:
    """保存 Cookie 到文件"""
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(cookie.strip() + "\n", encoding="utf-8")


def ensure_dirs() -> None:
    """确保所有需要的目录存在"""
    for d in [CONFIG_DIR, DATA_DIR, LOG_DIR]:
        d.mkdir(parents=True, exist_ok=True)
