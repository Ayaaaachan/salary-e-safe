"""
工银薪e安 - 合成银行流水数据生成器
合成数据回放，非精度承诺。仅用于仿真与答辩演示，绝不代表真实客户数据。

用法:
    python src/gen_data.py            # 使用 params.json 默认参数
    python src/gen_data.py --n 200 --m 12 --seed 20260926

输出:
    data/users.csv
    data/transactions.csv

【本轮关键修改】
1. 删除全部 `if balance >= amt` 判断，允许余额为负（真实透支）。
   这是 unfreeze 信号的唯一来源，不删则校准层空转。
2. gen_transactions_for_user 不再追踪余额、不输出 balance_after。
   余额链由 gen_data() 在按日期严格排序后一次性累计，保证与日期顺序永久自洽。
3. 期初余额 = 月薪 × params.data.initial_balance_months（唯一口径）。
"""
import os
import sys
import json
import argparse
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from io import StringIO

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_params():
    with open(os.path.join(ROOT, "params.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def month_days(year, month):
    """计算某年某月的天数（正确处理跨年）"""
    if month == 12:
        nxt = datetime(year + 1, 1, 1)
    else:
        nxt = datetime(year, month + 1, 1)
    return (nxt - timedelta(days=1)).day


def is_festival(date, festivals):
    """判断日期是否在某个节日脉冲窗口内，返回乘数或 None"""
    for fest in festivals.values():
        fm, fd = fest["month"], fest["day"]
        center = datetime(date.year, fm, fd)
        if abs((date - center).days) <= fest["window_days"]:
            return fest["multiplier"]
    return None


def gen_users(n_users, rng, params):
    """生成用户画像"""
    p = params["data"]
    sal_cfg = p["salary"]
    rent_cfg = p["rent"]
    cred_cfg = p["credit_repayment"]

    users = []
    for i in range(n_users):
        # 月薪：对数正态分布，裁剪到 [min, max]
        while True:
            salary = rng.lognormal(mean=sal_cfg["mu"], sigma=sal_cfg["sigma"])
            if sal_cfg["min"] <= salary <= sal_cfg["max"]:
                break
        salary = round(salary, 2)

        payday = int(rng.choice(p["payday_options"]))

        # 房租占比：以 1/3 为中心的截断正态
        while True:
            rent_ratio = rng.normal(rent_cfg["center"], rent_cfg["sigma"])
            if rent_cfg["ratio_min"] <= rent_ratio <= rent_cfg["ratio_max"]:
                break

        # 信用还款
        credit_count = int(rng.integers(cred_cfg["min_count"], cred_cfg["max_count"] + 1))
        credit_ratio = round(float(rng.uniform(cred_cfg["ratio_min"], cred_cfg["ratio_max"])), 4)

        users.append({
            "user_id": f"U{i+1:04d}",
            "salary": salary,
            "payday": payday,
            "rent_ratio": round(rent_ratio, 4),
            "rent_amount": round(salary * rent_ratio, 2),
            "credit_count": credit_count,
            "credit_ratio": credit_ratio,
            "credit_amount": round(salary * credit_ratio, 2),
        })
    return pd.DataFrame(users)


def gen_transactions_for_user(user, start_date, n_months, rng, params):
    """为单个用户生成 n_months 的流水。

    【本轮关键修改】
    1. 删除全部 `if balance >= amt` 判断，允许余额为负（真实透支）。
       这是 unfreeze 信号的唯一来源，不删则校准层空转。
    2. 本函数不再追踪余额、不输出 balance_after。
       余额链由 gen_data() 在按日期严格排序后一次性累计，保证与日期顺序永久自洽。
    """
    p = params["data"]
    effects = p["spending_effects"]
    large_cfg = p["large_expense"]
    festivals = p["festivals"]
    offset = p["rigid_offset_days"]
    daily_base_ratio = p["daily_spending_base_ratio"]

    salary = user["salary"]
    payday = user["payday"]
    rent_amt = user["rent_amount"]
    credit_total = user["credit_amount"]
    credit_count = user["credit_count"]

    txs = []

    for m in range(n_months):
        # 计算当前年月
        cur = datetime.strptime(start_date, "%Y-%m-%d")
        month_offset = m
        year = cur.year + (cur.month - 1 + month_offset) // 12
        month = (cur.month - 1 + month_offset) % 12 + 1
        ndays = month_days(year, month)

        actual_payday = min(payday, ndays)
        payday_date = datetime(year, month, actual_payday)

        # --- 1. 发薪入账 ---
        txs.append({
            "date": payday_date.strftime("%Y-%m-%d"),
            "amount": round(salary, 2),
            "type": "salary",
        })

        # --- 2. 房租（发薪日后 ±offset 天内）---
        rent_day = actual_payday + int(rng.integers(-offset, offset + 1))
        rent_day = max(1, min(rent_day, ndays))
        txs.append({
            "date": datetime(year, month, rent_day).strftime("%Y-%m-%d"),
            "amount": round(-rent_amt, 2),
            "type": "rent",
        })

        # --- 3. 信用还款（1~2 笔，分散在月中）---
        per_credit = credit_total / credit_count
        for _ in range(credit_count):
            base_day = actual_payday + int(rng.integers(7, 21))
            cred_day = base_day + int(rng.integers(-offset, offset + 1))
            cred_day = max(1, min(cred_day, ndays))
            txs.append({
                "date": datetime(year, month, cred_day).strftime("%Y-%m-%d"),
                "amount": round(-per_credit, 2),
                "type": "credit_repayment",
            })

        # --- 4. 每日随机消费（带三种真实效应）---
        for day in range(1, ndays + 1):
            d = datetime(year, month, day)
            weekday = d.weekday()

            base = salary * daily_base_ratio

            days_after_pay = (d - payday_date).days
            if 0 <= days_after_pay < effects["post_payday_peak_days"]:
                base *= effects["post_payday_multiplier"]

            if weekday >= 5:
                base *= effects["weekend_multiplier"]

            if (ndays - day) < effects["month_end_days"]:
                base *= effects["month_end_contraction"]

            fest_mult = is_festival(d, festivals)
            if fest_mult:
                base *= fest_mult

            n_tx = int(rng.integers(1, 4))
            for _ in range(n_tx):
                amt = round(abs(rng.normal(base, base * 0.3)), 2)
                if amt <= 0:
                    continue
                txs.append({
                    "date": d.strftime("%Y-%m-%d"),
                    "amount": -amt,
                    "type": "spending",
                })

        # --- 5. 偶发大额支出（医疗/人情）---
        if rng.random() < large_cfg["prob_per_month"]:
            amt_ratio = rng.uniform(large_cfg["amount_min_ratio"], large_cfg["amount_max_ratio"])
            big_amt = salary * amt_ratio
            big_day = int(rng.integers(1, ndays + 1))
            txs.append({
                "date": datetime(year, month, big_day).strftime("%Y-%m-%d"),
                "amount": round(-big_amt, 2),
                "type": "large_expense",
            })

    return pd.DataFrame(txs)


def gen_data(n_users, n_months, seed):
    """主入口：生成 users.csv 和 transactions.csv

    【本轮关键修改】余额链在全部交易生成完毕后、按
    (user_id, date, 同日类型顺序) 统一累计，期初余额 = 月薪 × initial_balance_months。
    这是全项目唯一的余额口径，不再有"代码一套、产物一套"的可能。
    """
    params = load_params()
    rng = np.random.default_rng(seed)

    users_df = gen_users(n_users, rng, params)
    start_date = params["data"]["start_date"]
    init_months = params["data"]["initial_balance_months"]

    all_txs = []
    for _, user in users_df.iterrows():
        tx_df = gen_transactions_for_user(user, start_date, n_months, rng, params)
        if len(tx_df) == 0:
            continue
        tx_df.insert(0, "user_id", user["user_id"])
        tx_df["_init_balance"] = round(user["salary"] * init_months, 2)
        all_txs.append(tx_df)

    txs_df = pd.concat(all_txs, ignore_index=True)

    # 同日排序约定：salary -> rent -> credit_repayment -> spending -> large_expense
    TYPE_ORDER = {"salary": 0, "rent": 1, "credit_repayment": 2,
                  "spending": 3, "large_expense": 4}
    txs_df["_t"] = txs_df["type"].map(TYPE_ORDER).fillna(9).astype(int)
    txs_df = txs_df.sort_values(["user_id", "date", "_t"], kind="stable").reset_index(drop=True)

    # 统一重算余额链（唯一口径）
    txs_df["balance_after"] = 0.0
    for uid, idx in txs_df.groupby("user_id", sort=False).groups.items():
        sub = txs_df.loc[idx]
        init = float(sub["_init_balance"].iloc[0])
        txs_df.loc[idx, "balance_after"] = (init + sub["amount"].cumsum()).round(2).values

    txs_df = txs_df.drop(columns=["_t", "_init_balance"])

    # ---- 生成后立即自查（不通过就报错，不要静默继续）----
    min_bal = txs_df["balance_after"].min()
    print(f"[合成数据] users={len(users_df)}, transactions={len(txs_df)}")
    print(f"[自查] 全局最小余额 = {min_bal:.2f}（必须为负数，否则 unfreeze 信号是死的）")
    print(f"[合成数据回放，非精度承诺] seed={seed}")
    if min_bal >= 0:
        raise RuntimeError(
            "余额全程未跌破 0 → unfreeze_flag 恒为 0 → 校准层将空转。"
            "请调小 params.json 的 data.daily_spending_base_ratio 后重跑。"
        )
    # 注意：文件写入由根目录脚本负责（src/ 内写盘被沙箱拦截）
    return users_df, txs_df


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=None, help="用户数")
    parser.add_argument("--m", type=int, default=None, help="月数")
    parser.add_argument("--seed", type=int, default=None, help="随机种子")
    args = parser.parse_args()

    params = load_params()
    n = args.n or params["data"]["n_users"]
    m = args.m or params["data"]["n_months"]
    s = args.seed if args.seed is not None else params["random_seed"]

    gen_data(n, m, s)
