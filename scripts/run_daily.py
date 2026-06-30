#!/usr/bin/env python3
"""每日定时任务入口脚本

读取数据库中已启用的关键词，逐批查询指数并保存，最后滚动清理过期数据。
建议用 cron 每天凌晨 2-3 点执行（避开网站高峰）。

cron 示例：
    0 3 * * * /opt/zhishu/venv/bin/python /opt/zhishu/scripts/run_daily.py
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 让脚本能直接运行（不依赖 PYTHONPATH 设置）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import config
from src.crawler import BaiduIndexCrawler, CookieExpiredError
from src.db import Database


def setup_logging(log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    # 固定文件名，交给系统 logrotate 按天滚动、保留 N 天（见 install.sh）。
    log_file = log_dir / "daily.log"
    handlers: list[logging.Handler] = [logging.FileHandler(log_file, encoding="utf-8")]
    # 仅在交互式终端追加控制台输出；cron 下不重复写到 cron.log。
    if sys.stdout.isatty():
        handlers.append(logging.StreamHandler(sys.stdout))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )


def prune(db: Database, log: logging.Logger) -> None:
    """滚动清理过期的历史数据与运行记录。失败不影响主流程。"""
    try:
        stats = db.prune_old(config.RETENTION_DAYS)
        if stats["daily_index_deleted"] or stats["run_log_deleted"]:
            log.info(
                "清理完成: 删除 %d 条历史指数、%d 条运行记录（保留 %d 天）",
                stats["daily_index_deleted"], stats["run_log_deleted"], config.RETENTION_DAYS,
            )
    except Exception as e:
        log.warning("清理旧数据失败（忽略）: %s", e)


def main() -> int:
    parser = argparse.ArgumentParser(description="指数每日定时抓取")
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
        prune(db, log)
        return 2

    # 关键：把 Cipher-Text 一起传给爬虫——定时任务缺了它会被接口直接拦截。
    cipher = config.load_cipher_text()
    if not cipher:
        log.warning("未配置 Cipher-Text，请求可能被接口拦截；建议在后台补上")
    crawler = BaiduIndexCrawler(
        cookie=cookie,
        proxy=config.effective_proxy() or None,
        cipher_text=cipher or None,
    )
    success_count = 0
    fail_count = len(keywords)
    error_detail: str | None = None

    try:
        outcome = crawler.fetch_batch(
            keywords,
            area=args.area,
            days=args.days,
            batch_size=args.batch_size,
        )
        success_count = outcome.success_count
        fail_count = outcome.fail_count
        error_detail = outcome.error_summary()

        written = db.save_results(outcome.results, area=args.area)
        log.info("抓取完成: 成功 %d 个关键词，写入 %d 条数据", success_count, written)
        if error_detail:
            log.warning("部分关键词失败: %s", error_detail)
    except CookieExpiredError as e:
        error_detail = f"凭证失效: {e}"
        log.error(error_detail)
        log.error("请尽快在后台重新粘贴 Cookie 与 Cipher-Text")
        success_count, fail_count = 0, len(keywords)
    except Exception as e:
        error_detail = f"未预期错误: {e}"
        log.exception("抓取过程出错")
        success_count, fail_count = 0, len(keywords)
    finally:
        db.finish_run(run_id, success=success_count, fail=fail_count, error=error_detail)
        log.info("任务结束: 成功=%d 失败=%d", success_count, fail_count)
        prune(db, log)

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
