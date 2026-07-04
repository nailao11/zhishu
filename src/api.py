"""HTTP API 服务

提供 REST 接口供外部程序查询指数数据、管理关键词、更新凭证与代理。
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from . import config
from .crawler import IndexCrawler, CookieExpiredError, RateLimitError
from .db import Database

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="指数查询服务",
    description="基于 curl_cffi 的轻量级指数 API。中文管理后台请访问 /admin。",
    version="0.1.0",
)

bearer_scheme = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def get_db() -> Database:
    return Database(config.DB_PATH)


def get_crawler() -> IndexCrawler:
    cookie = config.load_cookie()
    cipher = config.load_cipher_text()
    return IndexCrawler(
        cookie=cookie,
        proxy=config.effective_proxy() or None,
        cipher_text=cipher or None,
    )


def verify_token(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> None:
    """Bearer Token 鉴权；未配置 ZHISHU_API_TOKEN 时跳过。"""
    if not config.API_TOKEN:
        return
    if not creds or (creds.scheme or "").lower() != "bearer":
        raise HTTPException(status_code=401, detail="缺少 Authorization Bearer Token")
    if not secrets.compare_digest(creds.credentials.encode(), config.API_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="Token 错误")


# ---------- 数据模型 ----------

class QueryRequest(BaseModel):
    keywords: list[str] = Field(..., description="关键词列表，单次最多 5 个", max_length=5)
    days: int = Field(30, description="最近 N 天数据，常用 7/30/90/180/365", ge=1, le=365)
    area: int = Field(0, description="地区代码，0=全国")
    save: bool = Field(True, description="是否将结果存入数据库")


class KeywordsRequest(BaseModel):
    keywords: list[str] = Field(..., description="一个或多个关键词")


class KeywordUpdateRequest(BaseModel):
    enabled: bool = Field(..., description="是否参与每日定时抓取")


class CookieRequest(BaseModel):
    cookie: str = Field(..., description="完整的 Cookie 字符串")
    cipher_text: Optional[str] = Field(None, description="Cipher-Text 签名头（建议一起提交）")


class ProxyRequest(BaseModel):
    proxy: str = Field("", description="代理地址，留空表示清空（回退到环境变量或直连）")


class DiagnoseRequest(BaseModel):
    proxy: Optional[str] = Field(None, description="可选代理 URL；留空 = 直连测试")
    cookie: Optional[str] = Field(None, description="可选 Cookie 字符串；不传则只测 IP")
    cipher_text: Optional[str] = Field(None, description="可选 Cipher-Text 头，从同一请求复制")
    warmup: bool = Field(True, description="是否先访问主页预热")
    timeout: int = Field(90, description="超时秒数")


# ---------- 接口 ----------

@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/admin")


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
async def admin_page():
    admin_file = STATIC_DIR / "admin.html"
    if not admin_file.exists():
        return HTMLResponse(
            "<h1>admin.html 不存在</h1><p>请确认 src/static/admin.html 已部署</p>",
            status_code=500,
        )
    return FileResponse(admin_file)


@app.get("/api/health")
async def health(db: Database = Depends(get_db)):
    """存活探测（无鉴权，不返回配置细节）。"""
    try:
        db.list_keywords()
        return {"status": "ok", "time": datetime.now().isoformat(timespec="seconds")}
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})


def _do_diagnose(
    proxy: Optional[str],
    cookie: Optional[str],
    warmup: bool,
    timeout: int,
    cipher_text: Optional[str] = None,
) -> dict:
    """执行诊断：实发一次请求，定位卡在哪一步。代理按传入值处理，不套用已保存配置。"""
    import time as _time
    from curl_cffi import requests as cffi_requests
    from .crawler import INDEX_API_URL, HOME_URL, DEFAULT_HEADERS, HOME_HEADERS

    eff_proxy = (proxy or "").strip() or None
    cookie_str = (cookie or "").strip()
    use_cookie = bool(cookie_str)
    cipher_str = (cipher_text or "").strip()

    result = {
        "tested_at": datetime.now().isoformat(timespec="seconds"),
        "proxy": eff_proxy or "（直连，未走代理）",
        "timeout_seconds": timeout,
        "use_cookie": use_cookie,
        "cookie_length": len(cookie_str) if use_cookie else 0,
        "use_cipher_text": bool(cipher_str),
        "cipher_text_length": len(cipher_str),
        "warmup": warmup,
    }

    if use_cookie:
        if len(cookie_str) < 50:
            result["verdict"] = (
                f"❌ Cookie 仅 {len(cookie_str)} 字符，看起来不完整。"
                "请从同一请求里复制完整 Cookie 整行（含 BDUSS 字段）。"
            )
            return result
        try:
            cookie_str.encode("ascii")
        except UnicodeEncodeError:
            result["verdict"] = "❌ Cookie 含非 ASCII 字符，HTTP 头不允许，请重新复制。"
            return result
        if "BDUSS=" not in cookie_str and "BDUSS_BFESS=" not in cookie_str:
            result["verdict"] = (
                "❌ Cookie 里没有 BDUSS / BDUSS_BFESS，说明复制时还没登录。"
                "请先登录目标站点再复制 Cookie。"
            )
            return result

    start = _time.time()
    try:
        session = cffi_requests.Session(impersonate="chrome120")
        if eff_proxy:
            session.proxies = {"http": eff_proxy, "https": eff_proxy}
            session.verify = False

        # 探测实际出口 IP，用于判断是否走了代理
        try:
            ip_resp = session.get(
                "http://ip-api.com/json/?fields=query,country,regionName,isp",
                timeout=min(timeout, 12),
            )
            ipj = ip_resp.json()
            result["egress_ip"] = ipj.get("query")
            result["egress_geo"] = " ".join(
                x for x in [ipj.get("country"), ipj.get("regionName")] if x
            )
            result["egress_isp"] = ipj.get("isp")
        except Exception as e:
            result["egress_error"] = str(e)[:150]

        if warmup:
            try:
                home_headers = {**HOME_HEADERS}
                if cookie_str:
                    home_headers["Cookie"] = cookie_str
                home_resp = session.get(HOME_URL, headers=home_headers, timeout=timeout)
                result["warmup_status"] = home_resp.status_code
            except Exception as e:
                result["warmup_error"] = str(e)[:200]

        params = {
            "area": "0",
            "word": '[[{"name":"苹果","wordType":1}]]',
            "days": "30",
        }
        req_headers = {**DEFAULT_HEADERS}
        if cookie_str:
            req_headers["Cookie"] = cookie_str
        if cipher_str:
            req_headers["Cipher-Text"] = cipher_str
        resp = session.get(
            INDEX_API_URL,
            params=params,
            headers=req_headers,
            timeout=timeout,
        )
        result["elapsed_seconds"] = round(_time.time() - start, 2)
        result["http_status"] = resp.status_code
        try:
            data = resp.json()
            status = data.get("status")
            message = data.get("message", "")
            result["api_status"] = status
            result["api_message"] = message
            via = "代理" if eff_proxy else "本机直连"
            if status == 0:
                result["verdict"] = "🎉 成功拿到数据，链路完全跑通。"
                result["data_preview"] = str(data.get("data", {}))[:300]
            elif status == 10000:
                if use_cookie:
                    result["verdict"] = "⚠️ 链路通，但 Cookie 无效或已过期，请重新复制 Cookie。"
                else:
                    result["verdict"] = f"✅ {via}的 IP 可用。再带上 Cookie 测一次完整流程。"
            elif status == 10018:
                if use_cookie:
                    result["verdict"] = f"❌ 带 Cookie 仍被拦截（10018），{via}的出口 IP 不可用。"
                else:
                    result["verdict"] = f"❌ {via}不带 Cookie 时被拦截（10018），建议带上 Cookie 再测一次。"
            else:
                result["verdict"] = f"⚠️ 未预期的状态码 {status}，接口可能有调整。"
        except Exception:
            result["api_status"] = None
            result["body_preview"] = resp.text[:500]
            result["verdict"] = "⚠️ 响应不是合法 JSON，可能被中间环节拦截。"
    except Exception as e:
        result["elapsed_seconds"] = round(_time.time() - start, 2)
        result["error_type"] = type(e).__name__
        result["error"] = str(e)[:300]
        if "timeout" in str(e).lower() or "timed out" in str(e).lower():
            result["verdict"] = f"❌ 超时（{result['elapsed_seconds']} 秒）。代理太慢或对端没响应，可加大 timeout 或更换代理。"
        else:
            result["verdict"] = f"❌ 网络层报错：{type(e).__name__}。可能是代理地址错误、代理不可用或 TLS 握手失败。"

    return result


@app.post("/api/diagnose", dependencies=[Depends(verify_token)])
def diagnose(req: DiagnoseRequest):
    """网络诊断。同步 def：阻塞请求交给线程池，避免卡住事件循环。"""
    return _do_diagnose(req.proxy, req.cookie, req.warmup, req.timeout, req.cipher_text)


# 阻塞网络请求必须用同步 def，由 FastAPI 放进线程池，避免卡死事件循环
@app.post("/api/query", dependencies=[Depends(verify_token)])
def query_keywords(req: QueryRequest, db: Database = Depends(get_db)):
    """实时查询指数。会触发一次真实的爬取请求。"""
    try:
        crawler = get_crawler()
    except (FileNotFoundError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        results = crawler.fetch_keywords(req.keywords, area=req.area, days=req.days)
    except CookieExpiredError as e:
        raise HTTPException(status_code=401, detail=f"Cookie 已失效: {e}")
    except RateLimitError as e:
        raise HTTPException(status_code=429, detail=f"请求过于频繁: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"查询失败: {e}")

    if req.save:
        try:
            db.save_results(results, area=req.area)
        except Exception as e:
            logger.error("保存结果失败: %s", e)

    return {
        "count": len(results),
        "results": [r.to_dict() for r in results],
    }


@app.get("/api/index/{keyword}", dependencies=[Depends(verify_token)])
async def get_index(
    keyword: str,
    start_date: Optional[str] = Query(None, description="起始日期 YYYY-MM-DD"),
    end_date: Optional[str] = Query(None, description="结束日期 YYYY-MM-DD"),
    area: int = Query(0),
    db: Database = Depends(get_db),
):
    """从数据库读取关键词的历史指数。"""
    rows = db.query_index(keyword, start_date=start_date, end_date=end_date, area=area)
    return {
        "keyword": keyword,
        "area": area,
        "count": len(rows),
        "data": rows,
    }


@app.get("/api/latest/{keyword}", dependencies=[Depends(verify_token)])
async def get_latest(keyword: str, area: int = 0, db: Database = Depends(get_db)):
    """读取关键词最新一天的指数值。"""
    row = db.latest_index(keyword, area=area)
    if not row:
        raise HTTPException(status_code=404, detail=f"未找到 {keyword} 的数据")
    return {"keyword": keyword, "area": area, **row}


@app.get("/api/keywords", dependencies=[Depends(verify_token)])
async def list_keywords(enabled_only: bool = False, db: Database = Depends(get_db)):
    return {"keywords": db.list_keywords(enabled_only=enabled_only)}


@app.post("/api/keywords", dependencies=[Depends(verify_token)])
async def add_keywords(req: KeywordsRequest, db: Database = Depends(get_db)):
    added = db.add_keywords(req.keywords)
    return {"added": added, "skipped": len(req.keywords) - added}


@app.patch("/api/keywords/{keyword}", dependencies=[Depends(verify_token)])
async def update_keyword(keyword: str, req: KeywordUpdateRequest, db: Database = Depends(get_db)):
    """启用/禁用关键词。禁用后不再参与定时抓取，历史数据保留。"""
    ok = db.set_keyword_enabled(keyword, req.enabled)
    if not ok:
        raise HTTPException(status_code=404, detail=f"关键词不存在: {keyword}")
    return {"keyword": keyword, "enabled": req.enabled}


@app.delete("/api/keywords/{keyword}", dependencies=[Depends(verify_token)])
async def delete_keyword(keyword: str, db: Database = Depends(get_db)):
    ok = db.remove_keyword(keyword)
    if not ok:
        raise HTTPException(status_code=404, detail=f"关键词不存在: {keyword}")
    return {"removed": keyword}


@app.post("/api/cookie", dependencies=[Depends(verify_token)])
def update_cookie(req: CookieRequest):
    """更新凭证：Cookie + Cipher-Text，保存前用新凭证试调一次确认有效。"""
    cookie = req.cookie.strip()
    cipher = (req.cipher_text or "").strip()

    if len(cookie) < 50:
        raise HTTPException(
            status_code=400,
            detail=f"Cookie 仅 {len(cookie)} 字符，看起来不完整。"
                   "请从同一请求里复制完整 Cookie 整行（含 BDUSS 字段）。",
        )
    try:
        cookie.encode("ascii")
    except UnicodeEncodeError:
        raise HTTPException(
            status_code=400,
            detail="Cookie 含非 ASCII 字符，HTTP 头不接受（多半粘进了示例里的中文标点）。",
        )
    if "BDUSS=" not in cookie and "BDUSS_BFESS=" not in cookie:
        raise HTTPException(
            status_code=400,
            detail="Cookie 里没有 BDUSS / BDUSS_BFESS 字段，说明复制时还没登录。"
                   "请先登录目标站点再复制 Cookie。",
        )

    try:
        test = IndexCrawler(
            cookie=cookie,
            proxy=config.effective_proxy() or None,
            cipher_text=cipher or None,
        )
        test.fetch_keywords(["苹果"], days=7)
    except CookieExpiredError:
        raise HTTPException(status_code=400, detail="凭证验证失败，Cookie 或 Cipher-Text 可能已失效，请重新获取")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"凭证验证失败: {e}")

    config.save_cookie(cookie)
    if cipher:
        config.save_cipher_text(cipher)

    return {
        "status": "ok",
        "message": "凭证已更新并验证通过",
        "cookie_length": len(cookie),
        "cipher_text_length": len(cipher),
    }


@app.get("/api/credentials/status", dependencies=[Depends(verify_token)])
async def credentials_status():
    """查看当前凭证状态：Cookie 是否配置、Cipher-Text 生成于何时/已用多久。"""
    cookie_exists = config.COOKIE_FILE.exists()
    cookie_len = 0
    cookie_ok = False
    if cookie_exists:
        try:
            c = config.load_cookie()
            cookie_len = len(c)
            cookie_ok = True
        except Exception:
            cookie_ok = False

    cipher = config.load_cipher_text()
    info = config.parse_cipher_text_info(cipher) if cipher else {
        "format_ok": False, "reason": "未配置 Cipher-Text",
    }

    return {
        "cookie": {
            "configured": cookie_exists,
            "valid_format": cookie_ok,
            "length": cookie_len,
        },
        "cipher_text": {
            "configured": bool(cipher),
            "length": len(cipher),
            "info": info,
        },
    }


@app.get("/api/proxy", dependencies=[Depends(verify_token)])
async def get_proxy():
    """查看代理设置：后台保存的值，以及当前实际生效的代理。"""
    saved = config.load_proxy()
    effective = config.effective_proxy()
    source = "saved" if saved else ("env" if config.HTTP_PROXY else "none")
    return {"saved": saved, "effective": effective, "source": source}


@app.post("/api/proxy", dependencies=[Depends(verify_token)])
async def update_proxy(req: ProxyRequest):
    """保存代理地址。留空表示清空（回退到环境变量或直连）。"""
    proxy = (req.proxy or "").strip()
    if proxy:
        low = proxy.lower()
        if not (low.startswith("http://") or low.startswith("https://") or low.startswith("socks5://")):
            raise HTTPException(
                status_code=400,
                detail="代理格式不对，应以 http:// / https:// / socks5:// 开头",
            )
    config.save_proxy(proxy)
    return {"status": "ok", "saved": proxy, "effective": config.effective_proxy()}


@app.get("/api/runs", dependencies=[Depends(verify_token)])
async def get_runs(limit: int = 20, db: Database = Depends(get_db)):
    """查看最近的定时任务运行记录。"""
    return {"runs": db.recent_runs(limit=limit)}


def main():
    """命令行入口：python -m src.api"""
    import uvicorn

    config.ensure_dirs()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if not config.API_TOKEN:
        logger.warning("未设置 ZHISHU_API_TOKEN，所有接口无需鉴权即可访问！请在 .env 里配置")
    uvicorn.run(
        "src.api:app",
        host=config.API_HOST,
        port=config.API_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
