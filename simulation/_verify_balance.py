"""工银薪e安 - 数据自检脚本（合成数据回放，非精度承诺）

用法（在项目根目录执行）:
    python _verify_balance.py

通过标准:
    [1] 余额链断裂笔数 == 0
    [2] 全局最小余额 < 0
    [4] unfreeze_flag 均值 in [0.15, 0.30]
不通过就不要进入 simulate.py。
"""
import os
import sys
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))


def main():
    tx_path = os.path.join(ROOT, "data", "transactions.csv")
    feat_path = os.path.join(ROOT, "data", "features.parquet")

    if not os.path.exists(tx_path):
        print("[FAIL] data/transactions.csv 不存在，请先运行 src/gen_data.py")
        return 1

    tx = pd.read_csv(tx_path, encoding="utf-8-sig")
    tx["date"] = pd.to_datetime(tx["date"])
    # stable sort 保留同日内的生成顺序（与 gen_data 的 salary→rent→credit→spending 一致）
    tx = tx.sort_values(["user_id", "date"], kind="stable").reset_index(drop=True)

    bad = 0
    for uid, g in tx.groupby("user_id"):
        g = g.sort_values("date", kind="stable")
        if len(g) < 2:
            continue
        # 反推初始余额 = balance_after[0] - amount[0]，再全程 cumsum 比较
        # （不能用 cumsum().iloc[1:]，会把首笔 amount 重复累加）
        est_init = g["balance_after"].iloc[0] - g["amount"].iloc[0]
        expected = est_init + g["amount"].cumsum().values
        bad += int((np.abs(expected - g["balance_after"].values) > 0.02).sum())

    min_bal = tx["balance_after"].min()
    neg_users = int((tx.groupby("user_id")["balance_after"].min() < 0).sum())
    n_users = tx["user_id"].nunique()

    print(f"[1] 余额链断裂笔数        : {bad}      (期望 0)")
    print(f"[2] 全局最小余额          : {min_bal:.2f}   (期望 负数)")
    print(f"[3] 出现过负余额的用户数   : {neg_users}/{n_users}   (期望 > 0)")

    rate = None
    if os.path.exists(feat_path):
        f = pd.read_parquet(feat_path)
        rate = float(f["unfreeze_flag"].mean())
        print(f"[4] unfreeze_flag 均值    : {rate:.4f}   (期望 0.15 ~ 0.30)")
        print(f"[5] unfreeze_day != -1 行数: {int((f['unfreeze_day'] != -1).sum())}")
        print(f"[6] 特征窗口数            : {len(f)}")
        if "balance_chain_ok" in f.columns:
            ok = int(f["balance_chain_ok"].sum())
            print(f"[7] 窗口余额链自洽        : {ok}/{len(f)}")
    else:
        print("[4] features.parquet 不存在，请先运行 src/pipeline.py")

    ok = (bad == 0) and (min_bal < 0) and (rate is not None and 0.15 <= rate <= 0.30)
    print("\n" + ("[PASS] 数据自检通过" if ok else "[FAIL] 未通过，请回到第 4 节调参"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
