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
CIPHER_FILE = Path(os.environ.get("ZHISHU_CIPHER_FILE", CONFIG_DIR / "cipher_text.txt"))
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

    # 50 字符以下基本是 "test" / "BAIDUID=xxx" 这种占位符；
    # 真实 Cookie 即使只剩必要字段一般也有几百字符。具体长度不强约束。
    if len(cookie) < 50:
        raise ValueError(
            f"Cookie 长度仅 {len(cookie)} 字符，看起来是占位符或不完整。"
            f"请从浏览器 DevTools 复制完整的 Cookie 整行（包含 BDUSS 字段）写入 {COOKIE_FILE}"
        )

    # HTTP Header 必须 ASCII；占位符里的中文括号会导致 UnicodeEncodeError
    try:
        cookie.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError(
            f"Cookie 含有非 ASCII 字符（中文/中文标点），HTTP 头不允许。"
            f"这通常是因为粘错了示例占位符。请重新从浏览器 DevTools 复制完整 Cookie。"
        )

    # BDUSS 是百度的登录态 token（BDUSS_BFESS 是同义的 BFE 边缘版本）。
    # 两者都没有 = Cookie 抓取时账号没登录，发出去百度会直接返回"未登录"。
    # 这里早判定一下，给用户更精确的提示。
    if "BDUSS=" not in cookie and "BDUSS_BFESS=" not in cookie:
        raise ValueError(
            "Cookie 里没有 BDUSS / BDUSS_BFESS 字段，说明抓 Cookie 时浏览器还没登录百度账号。"
            "请先在浏览器里登录 https://index.baidu.com 之后再 F12 复制 Cookie。"
        )

    return cookie


def save_cookie(cookie: str) -> None:
    """保存 Cookie 到文件"""
    COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
    COOKIE_FILE.write_text(cookie.strip() + "\n", encoding="utf-8")


def load_cipher_text() -> str:
    """读取 Cipher-Text。文件不存在或为空时返回空字符串（不强制要求）。"""
    if not CIPHER_FILE.exists():
        return ""
    text = CIPHER_FILE.read_text(encoding="utf-8").strip()
    if not text:
        return ""
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not lines:
        return ""
    return lines[0]


def save_cipher_text(cipher_text: str) -> None:
    """保存 Cipher-Text 到文件"""
    CIPHER_FILE.parent.mkdir(parents=True, exist_ok=True)
    CIPHER_FILE.write_text(cipher_text.strip() + "\n", encoding="utf-8")


def parse_cipher_text_validity(cipher_text: str) -> dict:
    """解析 Cipher-Text 的有效期。格式：<ms1>_<ms2>_<encrypted>"""
    import time as _time
    if not cipher_text or "_" not in cipher_text:
        return {"valid": False, "reason": "格式不对"}
    parts = cipher_text.split("_", 2)
    if len(parts) < 3:
        return {"valid": False, "reason": "格式不对（应该是 ts1_ts2_data）"}
    try:
        ts1 = int(parts[0])
        ts2 = int(parts[1])
    except ValueError:
        return {"valid": False, "reason": "时间戳不是数字"}

    now_ms = int(_time.time() * 1000)
    # 取两个时间戳里较大的一个作为过期时间
    expire_ms = max(ts1, ts2)
    issue_ms = min(ts1, ts2)
    remaining_sec = (expire_ms - now_ms) / 1000
    return {
        "valid": remaining_sec > 0,
        "issued_at": issue_ms,
        "expires_at": expire_ms,
        "remaining_seconds": int(remaining_sec),
        "remaining_human": _human_duration(int(remaining_sec)),
    }


def _human_duration(seconds: int) -> str:
    if seconds <= 0:
        return "已过期"
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        return f"{seconds // 60} 分钟"
    h = seconds // 3600
    m = (seconds % 3600) // 60
    return f"{h} 小时 {m} 分钟" if m else f"{h} 小时"


def ensure_dirs() -> None:
    """确保所有需要的目录存在"""
    for d in [CONFIG_DIR, DATA_DIR, LOG_DIR]:
        d.mkdir(parents=True, exist_ok=True)
