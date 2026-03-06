"""
StockTrace — GitHub Actions 定时更新脚本
读取 data/recommendations.json，拉取行情，计算收益，回写 JSON
"""
import json
import os
import logging
import time
from datetime import datetime

import akshare as ak
import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


# ========== 文件读写 ==========

def load_json(filename):
    filepath = os.path.join(DATA_DIR, filename)
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_json(filename, data):
    filepath = os.path.join(DATA_DIR, filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    logger.info(f"已写入 {filepath}")


# ========== 股票数据 ==========

def normalize_code(code: str) -> str:
    code = code.strip().upper()
    for prefix in ["SH", "SZ", "BJ"]:
        code = code.replace(prefix, "")
    for suffix in [".SH", ".SZ", ".BJ"]:
        code = code.replace(suffix, "")
    return code.strip(".").zfill(6)


def get_stock_name(code: str) -> str:
    """获取股票名称，带重试"""
    code = normalize_code(code)
    for attempt in range(3):
        try:
            df = ak.stock_individual_info_em(symbol=code)
            if df is not None and not df.empty:
                name_row = df[df["item"] == "股票简称"]
                if not name_row.empty:
                    return str(name_row.iloc[0]["value"])
        except Exception as e:
            logger.warning(f"获取 {code} 名称失败(尝试{attempt+1}): {e}")
            if attempt < 2:
                time.sleep(2)

    # 备用方案
    try:
        df = ak.stock_zh_a_spot_em()
        row = df[df["代码"] == code]
        if not row.empty:
            return str(row.iloc[0]["名称"])
    except Exception as e:
        logger.warning(f"备用方案获取 {code} 名称也失败: {e}")
    return ""


def fetch_prices(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    获取历史日K行情（前复权）
    日期格式: YYYYMMDD
    """
    code = normalize_code(code)
    for attempt in range(3):
        try:
            df = ak.stock_zh_a_hist(
                symbol=code, period="daily",
                start_date=start_date, end_date=end_date,
                adjust="qfq"
            )
            if df is not None and not df.empty:
                df = df.rename(columns={
                    "日期": "trade_date",
                    "收盘": "close_price",
                    "开盘": "open_price",
                    "最高": "high_price",
                    "最低": "low_price",
                })
                df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y-%m-%d")
                return df[["trade_date", "close_price"]]
            return pd.DataFrame()
        except Exception as e:
            logger.warning(f"拉取 {code} 行情失败(尝试{attempt+1}): {e}")
            if attempt < 2:
                time.sleep(3)
    return pd.DataFrame()


# ========== 主逻辑 ==========

def main():
    logger.info("===== StockTrace 行情更新开始 =====")

    # 1. 加载推荐数据
    recs_data = load_json("recommendations.json")
    if not recs_data or not recs_data.get("recommendations"):
        logger.info("暂无推荐记录，跳过更新")
        return

    # 2. 加载已有的收益数据
    returns_data = load_json("returns.json") or {}

    today = datetime.now().strftime("%Y%m%d")
    active_count = 0
    updated_count = 0

    for rec in recs_data["recommendations"]:
        if not rec.get("is_active", True):
            continue

        active_count += 1
        code = normalize_code(rec["stock_code"])
        rec_id = str(rec["id"])
        logger.info(f"处理: {rec.get('stock_name', code)} ({code}) [ID={rec_id}]")

        # 补全股票名称
        if not rec.get("stock_name") or rec["stock_name"] == code:
            name = get_stock_name(code)
            if name:
                rec["stock_name"] = name
                logger.info(f"  股票名称补全: {name}")

        # 拉取行情
        start = rec["recommend_date"].replace("-", "")
        df = fetch_prices(code, start, today)

        if df.empty:
            logger.warning(f"  未获取到行情数据，跳过")
            continue

        # 补全推荐价格
        if not rec.get("recommend_price") or rec["recommend_price"] == 0:
            rec["recommend_price"] = round(float(df.iloc[0]["close_price"]), 2)
            logger.info(f"  推荐价格补全: {rec['recommend_price']}")

        base_price = rec["recommend_price"]
        if base_price <= 0:
            logger.warning(f"  推荐价格异常({base_price})，跳过")
            continue

        # 计算每日收益
        history = []
        for _, row in df.iterrows():
            close = round(float(row["close_price"]), 2)
            ret = round((close - base_price) / base_price * 100, 2)
            history.append({
                "date": row["trade_date"],
                "price": close,
                "return_pct": ret
            })

        if history:
            returns_data[rec_id] = {
                "latest_price": history[-1]["price"],
                "latest_return": history[-1]["return_pct"],
                "latest_date": history[-1]["date"],
                "history": history
            }
            updated_count += 1
            logger.info(f"  最新: ¥{history[-1]['price']} / {history[-1]['return_pct']:+.2f}%")

        # 礼貌延时，避免被反爬
        time.sleep(1)

    # 3. 保存更新后的推荐数据（含补全的名称和价格）
    save_json("recommendations.json", recs_data)

    # 4. 保存收益数据
    save_json("returns.json", returns_data)

    # 5. 计算博主统计
    bloggers = compute_blogger_stats(recs_data, returns_data)
    save_json("bloggers.json", {"bloggers": bloggers})

    logger.info(f"===== 更新完成: {updated_count}/{active_count} 只股票 =====")


def compute_blogger_stats(recs_data, returns_data):
    """聚合博主推荐表现"""
    blogger_map = {}

    for rec in recs_data.get("recommendations", []):
        if not rec.get("is_active", True):
            continue

        blogger = rec["blogger"]
        if blogger not in blogger_map:
            blogger_map[blogger] = {
                "blogger": blogger,
                "channel": rec.get("channel", ""),
                "recommendations": []
            }

        rec_id = str(rec["id"])
        ret_info = returns_data.get(rec_id, {})
        latest_return = ret_info.get("latest_return")

        blogger_map[blogger]["recommendations"].append({
            "stock_code": rec["stock_code"],
            "stock_name": rec.get("stock_name", ""),
            "latest_return": latest_return,
        })

    bloggers = []
    for b in blogger_map.values():
        recs = b["recommendations"]
        total = len(recs)
        with_return = [r for r in recs if r["latest_return"] is not None]
        wins = sum(1 for r in with_return if r["latest_return"] > 0)
        returns_list = [r["latest_return"] for r in with_return]

        bloggers.append({
            "blogger": b["blogger"],
            "channel": b.get("channel", ""),
            "total_count": total,
            "win_count": wins,
            "win_rate": round(wins / len(with_return) * 100, 1) if with_return else 0,
            "avg_return": round(sum(returns_list) / len(returns_list), 2) if returns_list else 0,
            "max_return": round(max(returns_list), 2) if returns_list else 0,
            "min_return": round(min(returns_list), 2) if returns_list else 0,
        })

    # 按平均收益降序
    bloggers.sort(key=lambda x: x["avg_return"], reverse=True)
    return bloggers


if __name__ == "__main__":
    main()
