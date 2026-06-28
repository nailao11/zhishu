"""HTTP API 服务

提供 REST 接口供外部程序查询百度指数数据、管理关键词、更新 Cookie。
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from . import config
from .crawler import BaiduIndexCrawler, CookieExpiredError, RateLimitError
from .db import Database

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(
    title="百度指数查询服务",
    description="基于 curl_cffi 的轻量级百度指数 API。中文管理后台请访问 /admin。",
    version="0.1.0",
)

bearer_scheme = HTTPBearer(auto_error=False)


def get_db() -> Database:
    return Database(config.DB_PATH)


def get_crawler() -> BaiduIndexCrawler:
    cookie = config.load_cookie()
    return BaiduIndexCrawler(cookie=cookie, proxy=config.HTTP_PROXY or None)


def verify_token(
    creds: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> None:
    """Bearer Token 鉴权。若 ZHISHU_API_TOKEN 未配置则跳过鉴权。

    用 HTTPBearer 声明，让 Swagger UI 显示 Authorize 按钮。
    """
    if not config.API_TOKEN:
        return
    if not creds or (creds.scheme or "").lower() != "bearer":
        raise HTTPException(status_code=401, detail="缺少 Authorization Bearer Token")
    if creds.credentials != config.API_TOKEN:
        raise HTTPException(status_code=401, detail="Token 错误")


# ---------- 数据模型 ----------

class QueryRequest(BaseModel):
    keywords: list[str] = Field(..., description="关键词列表，单次最多 5 个", max_length=5)
    days: int = Field(30, description="最近 N 天数据，常用 7/30/90/180/365", ge=1, le=365)
    area: int = Field(0, description="地区代码，0=全国")
    save: bool = Field(True, description="是否将结果存入数据库")


class KeywordsRequest(BaseModel):
    keywords: list[str] = Field(..., description="一个或多个关键词")


class CookieRequest(BaseModel):
    cookie: str = Field(..., description="完整的 Cookie 字符串")


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
    try:
        kw_count = len(db.list_keywords())
        cookie_exists = config.COOKIE_FILE.exists()
        return {
            "status": "ok",
            "time": datetime.now().isoformat(timespec="seconds"),
            "keyword_count": kw_count,
            "cookie_configured": cookie_exists,
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"status": "error", "detail": str(e)})


@app.get("/api/diagnose", dependencies=[Depends(verify_token)])
async def diagnose(
    proxy: Optional[str] = Query(None, description="可选代理"),
    timeout: int = Query(90, description="超时秒数，住宅代理建议 60-120"),
    use_cookie: bool = Query(False, description="为 true 时带上已保存的 Cookie，模拟真实抓取"),
    warmup: bool = Query(True, description="先访问主页 warmup，模拟真实浏览器"),
):
    """诊断服务器（或经过代理后）到百度的连接，用 curl_cffi 模拟 Chrome 直接测试。

    use_cookie=false（默认）+ warmup=false: 测纯 IP 是否被风控
    use_cookie=true + warmup=true: 完全模拟真实抓取，看能不能拿到数据
    """
    import time as _time
    from curl_cffi import requests as cffi_requests
    from .crawler import BAIDU_INDEX_URL, BAIDU_HOME_URL, DEFAULT_HEADERS, HOME_HEADERS

    effective_proxy = (proxy or config.HTTP_PROXY or "").strip() or None
    result = {
        "tested_at": datetime.now().isoformat(timespec="seconds"),
        "via": "curl_cffi (Chrome TLS impersonation)",
        "proxy": effective_proxy or "（直连，未走代理）",
        "timeout_seconds": timeout,
        "use_cookie": use_cookie,
        "warmup": warmup,
    }

    cookie_str = ""
    if use_cookie:
        try:
            cookie_str = config.load_cookie()
            result["cookie_length"] = len(cookie_str)
        except Exception as e:
            result["verdict"] = (
                f"❌ 启用了 Cookie 但读取失败: {e}\n\n"
                "解决：SSH 到服务器执行 `sudo nano /opt/zhishu/config/cookies.txt`，"
                "把里面所有内容删干净，粘贴你从浏览器复制的真实 Cookie 后保存。"
            )
            return result

    start = _time.time()
    try:
        session = cffi_requests.Session(impersonate="chrome120")
        if effective_proxy:
            session.proxies = {"http": effective_proxy, "https": effective_proxy}
            session.verify = False

        # 先 warmup（模拟真实浏览器先访问主页）
        if warmup:
            try:
                home_headers = {**HOME_HEADERS}
                if cookie_str:
                    home_headers["Cookie"] = cookie_str
                home_resp = session.get(BAIDU_HOME_URL, headers=home_headers, timeout=timeout)
                result["warmup_status"] = home_resp.status_code
            except Exception as e:
                result["warmup_error"] = str(e)

        params = {
            "area": "0",
            "word": '[[{"name":"苹果","wordType":1}]]',
            "days": "30",
        }
        req_headers = {**DEFAULT_HEADERS}
        if cookie_str:
            req_headers["Cookie"] = cookie_str
        resp = session.get(
            BAIDU_INDEX_URL,
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
            result["baidu_status"] = status
            result["baidu_message"] = message
            if status == 0:
                result["verdict"] = "🎉 真的拿到数据了！项目可以正式跑通"
                result["data_preview"] = str(data.get("data", {}))[:300]
            elif status == 10000:
                if use_cookie:
                    result["verdict"] = "⚠️ IP 通过但 Cookie 无效，请重新去浏览器复制完整 Cookie"
                else:
                    via = "代理" if effective_proxy else "服务器直连"
                    result["verdict"] = f"✅ {via}的 IP 没被风控。再开 use_cookie=true 测一次完整流程。"
            elif status == 10018:
                via = "代理" if effective_proxy else "服务器"
                if use_cookie:
                    result["verdict"] = f"❌ 即使带 Cookie 也被 10018 风控。{via}的 IP 被深度拉黑。"
                else:
                    result["verdict"] = f"❌ {via}的 IP 没 Cookie 时被 10018 风控。建议再用 use_cookie=true 测一次——有时候带 Cookie 反而能过。"
            else:
                result["verdict"] = f"⚠️ 未预期的状态码 {status}，百度可能改了接口"
        except Exception:
            result["baidu_status"] = None
            result["body_preview"] = resp.text[:500]
            result["verdict"] = "⚠️ 响应不是合法 JSON，可能被中间设备拦截了"
    except Exception as e:
        result["elapsed_seconds"] = round(_time.time() - start, 2)
        result["error_type"] = type(e).__name__
        result["error"] = str(e)
        if "timeout" in str(e).lower() or "timed out" in str(e).lower():
            result["verdict"] = f"❌ 超时（{result['elapsed_seconds']}秒），代理太慢或百度没回应。试更高的 timeout 参数或换代理。"
        else:
            result["verdict"] = f"❌ 网络层报错：{type(e).__name__}。代理地址错误、代理服务挂了、或者 TLS 握手失败。"

    return result


@app.post("/api/query", dependencies=[Depends(verify_token)])
async def query_keywords(req: QueryRequest, db: Database = Depends(get_db)):
    """实时查询百度指数。会触发一次真实的爬取请求。"""
    try:
        crawler = get_crawler()
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        results = crawler.fetch_keywords(req.keywords, area=req.area, days=req.days)
    except CookieExpiredError as e:
        raise HTTPException(status_code=401, detail=f"Cookie 已失效: {e}")
    except RateLimitError as e:
        raise HTTPException(status_code=429, detail=f"百度限流: {e}")
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


@app.delete("/api/keywords/{keyword}", dependencies=[Depends(verify_token)])
async def delete_keyword(keyword: str, db: Database = Depends(get_db)):
    ok = db.remove_keyword(keyword)
    if not ok:
        raise HTTPException(status_code=404, detail=f"关键词不存在: {keyword}")
    return {"removed": keyword}


@app.post("/api/cookie", dependencies=[Depends(verify_token)])
async def update_cookie(req: CookieRequest):
    """通过 API 更新 Cookie。免去 SSH 登录服务器编辑文件的麻烦。"""
    cookie = req.cookie.strip()
    if len(cookie) < 50:
        raise HTTPException(status_code=400, detail="Cookie 长度过短，可能不正确")
    try:
        # 用新 Cookie 做一次测试请求验证可用——必须走配置的代理（否则用服务器 IP 永远失败）
        test = BaiduIndexCrawler(cookie=cookie, proxy=config.HTTP_PROXY or None)
        test.fetch_keywords(["百度"], days=7)
    except CookieExpiredError:
        raise HTTPException(status_code=400, detail="新 Cookie 验证失败，请重新获取")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cookie 验证失败: {e}")

    config.save_cookie(cookie)
    return {"status": "ok", "message": "Cookie 已更新并验证通过"}


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
    uvicorn.run(
        "src.api:app",
        host=config.API_HOST,
        port=config.API_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
