"""配置加载

从环境变量和文件中读取配置。凭证与代理单独放在 config/ 下的文本文件里，方便随时更新。
"""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"

COOKIE_FILE = Path(os.environ.get("ZHISHU_COOKIE_FILE", CONFIG_DIR / "cookies.txt"))
CIPHER_FILE = Path(os.environ.get("ZHISHU_CIPHER_FILE", CONFIG_DIR / "cipher_text.txt"))
PROXY_FILE = Path(os.environ.get("ZHISHU_PROXY_FILE", CONFIG_DIR / "proxy.txt"))
DB_PATH = Path(os.environ.get("ZHISHU_DB_PATH", DATA_DIR / "zhishu.db"))
LOG_DIR = Path(os.environ.get("ZHISHU_LOG_DIR", PROJECT_ROOT / "logs"))

# API 访问 Token（防止公网被随便调用）
API_TOKEN = os.environ.get("ZHISHU_API_TOKEN", "")

API_HOST = os.environ.get("ZHISHU_API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("ZHISHU_API_PORT", "8000"))
DEFAULT_DAYS = int(os.environ.get("ZHISHU_DEFAULT_DAYS", "30"))

# 历史数据与日志的保留天数；超过的会在每日任务里滚动清理
RETENTION_DAYS = int(os.environ.get("ZHISHU_RETENTION_DAYS", "45"))

# 关键词自动删除天数：添加满 N 天后每日任务自动删除，0 表示不自动删
KEYWORD_TTL_DAYS = int(os.environ.get("ZHISHU_KEYWORD_TTL_DAYS", "60"))

# 可选的默认 HTTP/SOCKS5 代理（让抓取走代理出口）
# 格式： http://user:pass@host:port  或  socks5://user:pass@host:port
# 这是环境变量级别的默认值；后台保存的 proxy.txt 优先级更高。
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

    # BDUSS / BDUSS_BFESS 是登录态字段，两者都没有说明复制 Cookie 时还没登录。
    # 这里提前判定，给出更精确的提示。
    if "BDUSS=" not in cookie and "BDUSS_BFESS=" not in cookie:
        raise ValueError(
            "Cookie 里没有 BDUSS / BDUSS_BFESS 字段，说明复制 Cookie 时还没登录账号。"
            "请先在浏览器里登录目标站点，再从同一请求里复制 Cookie。"
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
    """保存 Cipher-Text 到文件（覆盖写，不留旧值）"""
    CIPHER_FILE.parent.mkdir(parents=True, exist_ok=True)
    CIPHER_FILE.write_text(cipher_text.strip() + "\n", encoding="utf-8")


def load_proxy() -> str:
    """读取后台保存的代理地址。文件不存在或为空时返回空字符串。"""
    if not PROXY_FILE.exists():
        return ""
    for line in PROXY_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def save_proxy(proxy: str) -> None:
    """保存代理地址。传空字符串表示清空（回退到环境变量或直连）。"""
    PROXY_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROXY_FILE.write_text((proxy or "").strip() + "\n", encoding="utf-8")


def effective_proxy() -> str:
    """实际生效的代理：后台保存的优先，其次环境变量，都没有则直连。"""
    return load_proxy() or HTTP_PROXY


# Cipher-Text 视为「新鲜」的时长（小时）。它本身不含过期时间，实际有效性由
# 服务端按约每天轮换判定；超过这个时长就提示重新复制一次。
CIPHER_FRESH_HOURS = int(os.environ.get("ZHISHU_CIPHER_FRESH_HOURS", "24"))


def parse_cipher_text_info(cipher_text: str) -> dict:
    """解析 Cipher-Text 的生成时间。

    格式为 ``<ms1>_<ms2>_<密文>``：两段数字都是生成时刻的时间戳（毫秒），
    其中较大的一段是这条签名被生成/复制的时间。字符串里**没有过期时间**——
    令牌的有效性由服务端判定、约每天轮换一次。所以这里只能给出「生成于何时、
    已经用了多久」，据此提示是否该刷新，而不是伪造一个过期倒计时。
    """
    import time as _time

    if not cipher_text or "_" not in cipher_text:
        return {"format_ok": False, "reason": "格式不对，应为 数字_数字_编码"}
    parts = cipher_text.split("_", 2)
    if len(parts) < 3:
        return {"format_ok": False, "reason": "格式不对，应为 数字_数字_编码"}
    try:
        ts1 = int(parts[0])
        ts2 = int(parts[1])
    except ValueError:
        return {"format_ok": False, "reason": "开头两段不是数字时间戳"}

    generated_ms = max(ts1, ts2)
    now_ms = int(_time.time() * 1000)
    age_sec = max(0, int((now_ms - generated_ms) / 1000))

    import datetime as _dt
    generated_human = _dt.datetime.fromtimestamp(generated_ms / 1000).strftime("%Y-%m-%d %H:%M:%S")

    return {
        "format_ok": True,
        "generated_at": generated_ms,
        "generated_at_human": generated_human,
        "age_seconds": age_sec,
        "age_human": _human_duration(age_sec),
        "stale": age_sec > CIPHER_FRESH_HOURS * 3600,
    }


def _human_duration(seconds: int) -> str:
    if seconds < 60:
        return "刚刚"
    if seconds < 3600:
        return f"{seconds // 60} 分钟"
    if seconds < 86400:
        h = seconds // 3600
        m = (seconds % 3600) // 60
        return f"{h} 小时 {m} 分钟" if m else f"{h} 小时"
    d = seconds // 86400
    h = (seconds % 86400) // 3600
    return f"{d} 天 {h} 小时" if h else f"{d} 天"


def ensure_dirs() -> None:
    """确保所有需要的目录存在"""
    for d in [CONFIG_DIR, DATA_DIR, LOG_DIR]:
        d.mkdir(parents=True, exist_ok=True)
