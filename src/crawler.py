"""指数爬虫核心

使用 curl_cffi 以 Chrome 的 TLS 指纹发起请求，配合完整的浏览器请求头。
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

INDEX_API_URL = "https://index.baidu.com/api/SearchApi/index"
PTBK_URL = "https://index.baidu.com/Interface/ptbk"
HOME_URL = "https://index.baidu.com/v2/main/index.html"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://index.baidu.com/v2/main/index.html",
    "Origin": "https://index.baidu.com",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Connection": "keep-alive",
}

HOME_HEADERS = {
    "User-Agent": DEFAULT_HEADERS["User-Agent"],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Sec-Ch-Ua": DEFAULT_HEADERS["Sec-Ch-Ua"],
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Connection": "keep-alive",
}


class CookieExpiredError(Exception):
    """Cookie 过期或无效"""


class RateLimitError(Exception):
    """请求过于频繁，被限流"""


class IndexApiError(Exception):
    """接口通用错误"""


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


@dataclass
class BatchOutcome:
    """批量抓取结果。failures 每项为 ``{"keywords": [...], "error": "原因"}``。"""
    results: list[KeywordResult] = field(default_factory=list)
    failures: list[dict] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return len(self.results)

    @property
    def fail_count(self) -> int:
        return sum(len(f["keywords"]) for f in self.failures)

    def error_summary(self) -> str | None:
        """失败明细汇成一行；全部成功时返回 None。"""
        if not self.failures:
            return None
        return "；".join(
            f"{'、'.join(f['keywords'])}: {f['error']}" for f in self.failures
        )


class IndexCrawler:
    """指数爬虫，单 Cookie 模式。

    使用方式：
        crawler = IndexCrawler(cookie="your_cookie_string")
        results = crawler.fetch_keywords(["python", "java"], days=30)
    """

    def __init__(
        self,
        cookie: str,
        impersonate: str = "chrome120",
        proxy: str | None = None,
        verify_ssl: bool | None = None,
        cipher_text: str | None = None,
    ):
        if not cookie or not cookie.strip():
            raise ValueError("Cookie 不能为空")
        self.cookie = cookie.strip()
        self.cipher_text = (cipher_text or "").strip() or None
        self.proxy = (proxy or "").strip() or None
        self.session = requests.Session(impersonate=impersonate)
        if self.proxy:
            self.session.proxies = {"http": self.proxy, "https": self.proxy}
        # 走代理默认关闭证书校验（代理常做中间解密），直连默认开启
        if verify_ssl is None:
            verify_ssl = self.proxy is None
        self.session.verify = verify_ssl
        self._warmed_up = False

    def _warmup(self) -> None:
        """先访问一次主页再调 API，模拟浏览器访问顺序。"""
        if self._warmed_up:
            return
        try:
            headers = {**HOME_HEADERS, "Cookie": self.cookie}
            self.session.get(HOME_URL, headers=headers, timeout=30, allow_redirects=True)
            self._warmed_up = True
        except Exception as e:
            logger.warning("warmup 失败，继续直接调 API: %s", e)
            self._warmed_up = True  # 不重试

    def _headers(self) -> dict:
        headers = {**DEFAULT_HEADERS, "Cookie": self.cookie}
        if self.cipher_text:
            headers["Cipher-Text"] = self.cipher_text
        return headers

    @staticmethod
    def _decrypt(key: str, data: str) -> str:
        """指数数据的解密：基于 ptbk 的字符替换。

        ptbk 长度为偶数，前一半是密文字符表，后一半是对应的明文字符。
        """
        if len(key) % 2 != 0:
            raise IndexApiError(f"ptbk 长度异常（{len(key)}），无法解密")
        n = len(key) // 2
        return data.translate(str.maketrans(key[:n], key[n:]))

    @staticmethod
    def _parse_int_list(s: str) -> list[int]:
        result = []
        for v in s.split(","):
            try:
                result.append(int(v.strip()))
            except ValueError:
                result.append(0)
        return result

    @staticmethod
    def _item_keyword(item: dict) -> str:
        """从返回项里取出它对应的关键词名；取不到返回空串。"""
        words = item.get("word") or []
        if isinstance(words, list) and words:
            first = words[0] or {}
            if isinstance(first, dict):
                return str(first.get("name") or "")
        return ""

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
        self._warmup()
        word_param = [[{"name": kw, "wordType": 1}] for kw in keywords]
        params = {
            "area": str(area),
            "word": json.dumps(word_param, ensure_ascii=False),
            "days": str(days),
        }
        resp = self.session.get(
            INDEX_API_URL,
            params=params,
            headers=self._headers(),
            timeout=30,
        )
        if resp.status_code == 429:
            raise RateLimitError("请求过于频繁，被限流（HTTP 429）")
        if resp.status_code != 200:
            raise IndexApiError(f"HTTP {resp.status_code}: {resp.text[:200]}")

        try:
            data = resp.json()
        except Exception as e:
            raise IndexApiError(f"响应不是合法 JSON: {e}; body={resp.text[:200]}")

        status = data.get("status")
        # 接口有时返回 message=0 (int) 而不是字符串，统一转成字符串再判断
        raw_message = data.get("message", "")
        message = str(raw_message) if raw_message is not None else ""
        if status == 10000 or "未登录" in message or "not login" in message.lower():
            raise CookieExpiredError("凭证已失效，请重新获取 Cookie 与 Cipher-Text")
        if status == 10001:
            raise RateLimitError(f"请求过于频繁: {message}")
        if status != 0:
            raise IndexApiError(f"接口返回错误 status={status}: {message}")

        return data

    def _fetch_ptbk(self, uniqid: str) -> str:
        resp = self.session.get(
            PTBK_URL,
            params={"uniqid": uniqid},
            headers=self._headers(),
            timeout=30,
        )
        if resp.status_code != 200:
            raise IndexApiError(f"获取ptbk失败 HTTP {resp.status_code}")
        data = resp.json()
        ptbk = data.get("data", "")
        if not ptbk:
            raise IndexApiError("ptbk 为空，凭证可能已失效")
        return ptbk

    def fetch_keywords(
        self,
        keywords: list[str],
        area: int = 0,
        days: int = 30,
        sleep_between: tuple[float, float] = (0.8, 1.5),
    ) -> list[KeywordResult]:
        """查询关键词的指数。

        :param keywords: 关键词列表，单次最多 5 个（接口限制）
        :param area: 地区代码，0=全国，其他参考地区代码表
        :param days: 查询最近 N 天的数据，常用值 7/30/90/180/365
        :param sleep_between: 两次请求之间的随机睡眠区间（秒）
        :return: 每个关键词的查询结果
        """
        if len(keywords) > 5:
            raise ValueError("单次查询最多 5 个关键词，请分批调用")

        raw = self._fetch_raw(keywords, area, days)
        uniqid = raw["data"]["uniqid"]

        time.sleep(random.uniform(*sleep_between))
        ptbk = self._fetch_ptbk(uniqid)

        user_indexes = raw["data"]["userIndexes"]
        results: list[KeywordResult] = []

        # 优先按返回项自带的词名对齐；接口漏掉中间某个词时，纯按位置对齐会把
        # 后面的数据整体错位安到前面的词上。词名缺失时才退回按位置对齐。
        by_name: dict[str, dict] = {}
        for item in user_indexes:
            name = self._item_keyword(item)
            if name:
                by_name.setdefault(name, item)
        requested = set(keywords)

        for i, kw in enumerate(keywords):
            item = by_name.get(kw)
            if item is None and i < len(user_indexes):
                candidate = user_indexes[i]
                cand_name = self._item_keyword(candidate)
                # 位置兜底仅在该项没标词名、或词名不属于本次请求的其他词时使用
                if not cand_name or cand_name not in requested:
                    item = candidate
            if item is None:
                logger.warning("关键词 %s 未返回数据", kw)
                continue
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
    ) -> BatchOutcome:
        """批量查询，按 batch_size 切分并加延迟。

        CookieExpiredError 直接抛出；单个批次的其他异常记入 failures，不影响其余批次。
        """
        outcome = BatchOutcome()
        total_batches = (len(keywords) + batch_size - 1) // batch_size
        for i in range(0, len(keywords), batch_size):
            batch = keywords[i : i + batch_size]
            try:
                outcome.results.extend(self.fetch_keywords(batch, area=area, days=days))
                logger.info("批次 %d/%d 完成: %s", i // batch_size + 1, total_batches, batch)
            except CookieExpiredError:
                raise
            except RateLimitError as e:
                logger.warning("批次 %s 被限流，等待 60 秒后重试一次: %s", batch, e)
                time.sleep(60)
                try:
                    outcome.results.extend(self.fetch_keywords(batch, area=area, days=days))
                    logger.info("批次 %d/%d 重试成功: %s", i // batch_size + 1, total_batches, batch)
                except CookieExpiredError:
                    raise
                except Exception as e2:
                    logger.error("批次 %s 重试仍失败: %s", batch, e2)
                    outcome.failures.append({"keywords": list(batch), "error": str(e2)})
            except Exception as e:
                logger.error("批次 %s 查询失败: %s", batch, e)
                outcome.failures.append({"keywords": list(batch), "error": str(e)})

            if i + batch_size < len(keywords):
                time.sleep(random.uniform(*sleep_between_batch))

        return outcome
