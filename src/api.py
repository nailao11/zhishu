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
    return BaiduIndexCrawler(cookie=cookie)


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
async def diagnose():
    """诊断服务器到百度的连接，用项目的 curl_cffi 模拟 Chrome 直接测试。

    这个接口故意不带 Cookie，看百度返回什么状态码：
    - 0       接口居然不要登录就能访问（基本不可能）
    - 10000   未登录（正常情况，说明 IP 没问题，TLS指纹通过了）
    - 10018   触发风控（IP 被百度盯上了）
    - 其他    百度可能改了接口
    """
    from curl_cffi import requests as cffi_requests
    from .crawler import BAIDU_INDEX_URL, DEFAULT_HEADERS

    result = {
        "tested_at": datetime.now().isoformat(timespec="seconds"),
        "via": "curl_cffi (Chrome TLS impersonation)",
    }

    try:
        session = cffi_requests.Session(impersonate="chrome120")
        params = {
            "area": "0",
            "word": '[[{"name":"苹果","wordType":1}]]',
            "days": "30",
        }
        resp = session.get(
            BAIDU_INDEX_URL,
            params=params,
            headers=DEFAULT_HEADERS,
            timeout=30,
        )
        result["http_status"] = resp.status_code
        try:
            data = resp.json()
            status = data.get("status")
            message = data.get("message", "")
            result["baidu_status"] = status
            result["baidu_message"] = message
            if status == 10000:
                result["verdict"] = "✅ 服务器 IP 没被风控，Chrome TLS 指纹通过了。现在只需要一个有效的 Cookie 就能工作。"
            elif status == 10018:
                result["verdict"] = "❌ 服务器 IP 已经被百度风控，就算配上有效 Cookie 也用不了。需要换服务器或挂住宅代理。"
            elif status == 0:
                result["verdict"] = "✅ 居然不需要登录就能查（极罕见）"
            else:
                result["verdict"] = f"⚠️ 未预期的状态码 {status}，百度可能改了接口"
        except Exception:
            result["baidu_status"] = None
            result["body_preview"] = resp.text[:500]
            result["verdict"] = "⚠️ 响应不是合法 JSON，可能被中间设备拦截了"
    except Exception as e:
        result["error"] = str(e)
        result["verdict"] = "❌ 网络层就出错了，可能根本连不上百度"

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
        # 用新 Cookie 做一次测试请求验证可用
        test = BaiduIndexCrawler(cookie=cookie)
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
