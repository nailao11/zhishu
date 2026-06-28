"""百度指数爬虫核心

使用 curl_cffi 模拟真实 Chrome 浏览器的 TLS 指纹，
绕过百度的指纹反爬检测。
"""
from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from curl_cffi import requests

logger = logging.getLogger(__name__)

BAIDU_INDEX_URL = "https://index.baidu.com/api/SearchApi/index"
PTBK_URL = "https://index.baidu.com/Interface/ptbk"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://index.baidu.com/v2/main/index.html",
    "X-Requested-With": "XMLHttpRequest",
}


class CookieExpiredError(Exception):
    """Cookie 过期或无效"""


class RateLimitError(Exception):
    """触发百度限流"""


class BaiduIndexError(Exception):
    """百度指数 API 通用错误"""


@dataclass
class KeywordResult:
    keyword: str
    start_date: str
    end_date: str
    dates: list[str]
    all_index: list[int]
    pc_index: list[int]
    wise_index: list[int]

    def to_dict(self) -> dict:
        return {
            "keyword": self.keyword,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "dates": self.dates,
            "all": self.all_index,
            "pc": self.pc_index,
            "wise": self.wise_index,
        }

    def daily_points(self) -> list[dict]:
        """转换为按日期的数据点列表，方便入库"""
        points = []
        for i, date in enumerate(self.dates):
            points.append({
                "date": date,
                "all": self.all_index[i] if i < len(self.all_index) else 0,
                "pc": self.pc_index[i] if i < len(self.pc_index) else 0,
                "wise": self.wise_index[i] if i < len(self.wise_index) else 0,
            })
        return points


class BaiduIndexCrawler:
    """百度指数爬虫，单 Cookie 模式。

    使用方式：
        crawler = BaiduIndexCrawler(cookie="your_cookie_string")
        results = crawler.fetch_keywords(["python", "java"], days=30)
    """

    def __init__(
        self,
        cookie: str,
        impersonate: str = "chrome120",
        proxy: str | None = None,
    ):
        if not cookie or not cookie.strip():
            raise ValueError("Cookie 不能为空")
        self.cookie = cookie.strip()
        self.proxy = (proxy or "").strip() or None
        self.session = requests.Session(impersonate=impersonate)
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}

    def _headers(self) -> dict:
        return {**DEFAULT_HEADERS, "Cookie": self.cookie}

    @staticmethod
    def _decrypt(key: str, data: str) -> str:
        """百度指数的解密算法：基于 ptbk 的字符替换。

        ptbk 长度为偶数，前一半是密文字符表，后一半是对应的明文字符。
        """
        n = len(key) // 2
        cipher_chars = key[:n]
        plain_chars = key[n:]
        out = []
        for ch in data:
            idx = cipher_chars.find(ch)
            if idx >= 0:
                out.append(plain_chars[idx])
            else:
                out.append(ch)
        return "".join(out)

    @staticmethod
    def _parse_int_list(s: str) -> list[int]:
        result = []
        for v in s.split(","):
            v = v.strip()
            if not v or v == "":
                result.append(0)
                continue
            try:
                result.append(int(v))
            except ValueError:
                result.append(0)
        return result

    @staticmethod
    def _date_range(start: str, end: str) -> list[str]:
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        out = []
        cur = start_dt
        while cur <= end_dt:
            out.append(cur.strftime("%Y-%m-%d"))
            cur += timedelta(days=1)
        return out

    def _fetch_raw(self, keywords: list[str], area: int, days: int) -> dict:
        word_param = [[{"name": kw, "wordType": 1}] for kw in keywords]
        params = {
            "area": str(area),
            "word": json.dumps(word_param, ensure_ascii=False),
            "days": str(days),
        }
        resp = self.session.get(
            BAIDU_INDEX_URL,
            params=params,
            headers=self._headers(),
            timeout=30,
        )
        if resp.status_code == 429:
            raise RateLimitError("触发百度限流（HTTP 429）")
        if resp.status_code != 200:
            raise BaiduIndexError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            data = resp.json()
        except Exception as e:
            raise BaiduIndexError(f"响应不是合法 JSON: {e}; body={resp.text[:200]}")

        status = data.get("status")
        message = data.get("message", "")
        if status == 10000 or "未登录" in message or "not login" in message.lower():
            raise CookieExpiredError("Cookie 已失效，请重新获取并更新")
        if status == 10001:
            raise RateLimitError(f"请求过于频繁: {message}")
        if status != 0:
            raise BaiduIndexError(f"百度API错误 status={status}: {message}")

        return data

    def _fetch_ptbk(self, uniqid: str) -> str:
        resp = self.session.get(
            PTBK_URL,
            params={"uniqid": uniqid},
            headers=self._headers(),
            timeout=30,
        )
        if resp.status_code != 200:
            raise BaiduIndexError(f"获取ptbk失败 HTTP {resp.status_code}")
        data = resp.json()
        ptbk = data.get("data", "")
        if not ptbk:
            raise BaiduIndexError(f"ptbk 为空，可能Cookie失效: {data}")
        return ptbk

    def fetch_keywords(
        self,
        keywords: list[str],
        area: int = 0,
        days: int = 30,
        sleep_between: tuple[float, float] = (0.8, 1.5),
    ) -> list[KeywordResult]:
        """查询关键词的百度指数。

        :param keywords: 关键词列表，单次最多 5 个（百度限制）
        :param area: 地区代码，0=全国，其他参考百度地区代码表
        :param days: 查询最近 N 天的数据，常用值 7/30/90/180/365
        :param sleep_between: 两次请求之间的随机睡眠区间（秒）
        :return: 每个关键词的查询结果
        """
        if len(keywords) > 5:
            raise ValueError("单次查询最多 5 个关键词，请分批调用")

        raw = self._fetch_raw(keywords, area, days)
        uniqid = raw["data"]["uniqid"]

        # 适当延迟，模拟人类操作
        time.sleep(random.uniform(*sleep_between))
        ptbk = self._fetch_ptbk(uniqid)

        user_indexes = raw["data"]["userIndexes"]
        results: list[KeywordResult] = []

        for i, kw in enumerate(keywords):
            if i >= len(user_indexes):
                logger.warning("关键词 %s 未返回数据", kw)
                continue
            item = user_indexes[i]
            all_blob = item.get("all", {})
            pc_blob = item.get("pc", {})
            wise_blob = item.get("wise", {})

            start_date = all_blob.get("startDate", "")
            end_date = all_blob.get("endDate", "")
            if not start_date or not end_date:
                logger.warning("关键词 %s 缺少日期范围", kw)
                continue

            dates = self._date_range(start_date, end_date)
            all_data = self._parse_int_list(self._decrypt(ptbk, all_blob.get("data", "")))
            pc_data = self._parse_int_list(self._decrypt(ptbk, pc_blob.get("data", "")))
            wise_data = self._parse_int_list(self._decrypt(ptbk, wise_blob.get("data", "")))

            results.append(KeywordResult(
                keyword=kw,
                start_date=start_date,
                end_date=end_date,
                dates=dates,
                all_index=all_data,
                pc_index=pc_data,
                wise_index=wise_data,
            ))

        return results

    def fetch_batch(
        self,
        keywords: list[str],
        area: int = 0,
        days: int = 30,
        batch_size: int = 5,
        sleep_between_batch: tuple[float, float] = (2.0, 4.0),
    ) -> list[KeywordResult]:
        """批量查询，自动按 batch_size 切分并加延迟。

        适合定时任务一次跑几十上百个关键词。
        """
        all_results: list[KeywordResult] = []
        for i in range(0, len(keywords), batch_size):
            batch = keywords[i : i + batch_size]
            try:
                results = self.fetch_keywords(batch, area=area, days=days)
                all_results.extend(results)
                logger.info("批次 %d/%d 完成: %s", i // batch_size + 1, (len(keywords) + batch_size - 1) // batch_size, batch)
            except CookieExpiredError:
                raise
            except RateLimitError as e:
                logger.error("触发限流，等待 60 秒后重试一次: %s", e)
                time.sleep(60)
                try:
                    results = self.fetch_keywords(batch, area=area, days=days)
                    all_results.extend(results)
                except Exception as e2:
                    logger.error("重试仍失败，跳过批次 %s: %s", batch, e2)
            except Exception as e:
                logger.error("查询批次 %s 失败: %s", batch, e)

            # 批次之间随机睡眠
            if i + batch_size < len(keywords):
                time.sleep(random.uniform(*sleep_between_batch))

        return all_results
