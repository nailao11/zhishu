#!/usr/bin/env python3
"""每日定时任务入口脚本

读取数据库中已启用的关键词，逐批查询百度指数并保存。
建议用 cron 每天凌晨 2-3 点执行（避开网站高峰）。

cron 示例：
    0 3 * * * /opt/zhishu/venv/bin/python /opt/zhishu/scripts/run_daily.py >> /opt/zhishu/logs/daily.log 2>&1
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

# 让脚本能直接运行（不依赖 PYTHONPATH 设置）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.crawler import BaiduIndexCrawler, CookieExpiredError
from src.db import Database


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"daily_{datetime.now().strftime('%Y%m')}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="百度指数每日定时抓取")
    parser.add_argument("--days", type=int, default=config.DEFAULT_DAYS,
                        help="抓取最近 N 天的数据 (默认 30)")
    parser.add_argument("--area", type=int, default=0, help="地区代码 (默认 0=全国)")
    parser.add_argument("--batch-size", type=int, default=5, help="单次请求关键词数")
    args = parser.parse_args()

    config.ensure_dirs()
    setup_logging(config.LOG_DIR)
    log = logging.getLogger("daily")

    log.info("=" * 60)
    log.info("开始每日抓取任务，days=%d area=%d", args.days, args.area)

    db = Database(config.DB_PATH)
    keywords = [k["keyword"] for k in db.list_keywords(enabled_only=True)]

    if not keywords:
        log.warning("数据库中没有启用的关键词，结束任务")
        return 0

    log.info("需要抓取 %d 个关键词", len(keywords))
    run_id = db.start_run(len(keywords))

    try:
        cookie = config.load_cookie()
    except (FileNotFoundError, ValueError) as e:
        log.error("Cookie 加载失败: %s", e)
        db.finish_run(run_id, success=0, fail=len(keywords), error=str(e))
        return 2

    crawler = BaiduIndexCrawler(cookie=cookie, proxy=config.HTTP_PROXY or None)
    success_count = 0
    fail_count = 0
    fatal_error: str | None = None

    try:
        results = crawler.fetch_batch(
            keywords,
            area=args.area,
            days=args.days,
            batch_size=args.batch_size,
        )
        success_count = len(results)
        fail_count = len(keywords) - success_count

        written = db.save_results(results, area=args.area)
        log.info("抓取完成: 成功 %d 个关键词，写入 %d 条数据", success_count, written)
    except CookieExpiredError as e:
        fatal_error = f"Cookie 失效: {e}"
        log.error(fatal_error)
        log.error("⚠️  请尽快通过 API 或编辑 %s 更新 Cookie", config.COOKIE_FILE)
        fail_count = len(keywords) - success_count
    except Exception as e:
        fatal_error = f"未预期错误: {e}"
        log.exception("抓取过程出错")
        fail_count = len(keywords) - success_count
    finally:
        db.finish_run(run_id, success=success_count, fail=fail_count, error=fatal_error)
        log.info("任务结束: 成功=%d 失败=%d", success_count, fail_count)

    return 0 if not fatal_error and fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
