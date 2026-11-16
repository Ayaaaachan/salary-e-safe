"""工银薪e安 - 解冻率调参脚本（合成数据回放，非精度承诺）

自动扫描 params.json 里的 data.daily_spending_base_ratio，
找到让 unfreeze_flag 均值最接近目标的取值。

用法:
    python tune_unfreeze.py            # 只扫描并打印建议值
    python tune_unfreeze.py --apply    # 把建议值写回 params.json
"""
import argparse
import copy
import json
import os
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))
import gen_data as G
import pipeline as P


def evaluate(ratio, n_users, n_months, seed):
    """用指定 ratio 跑一遍 gen_data + pipeline，返回 unfreeze_flag 均值"""
    params = copy.deepcopy(G.load_params())
    params["data"]["daily_spending_base_ratio"] = ratio
    params["data"]["n_users"] = n_users

    G.load_params = lambda: copy.deepcopy(params)
    P.load_params = lambda: copy.deepcopy(params)

    _users, tx_df = G.gen_data(n_users, n_months, seed)

    df = tx_df.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["user_id", "date"]).reset_index(drop=True)
    df = P.dedup(df)
    df = P.fill_missing(df)
    df = P.detect_outlier(df)
    wins = P.window_by_payday(df)
    feats = P.build_features(wins, params)
    return float(feats["unfreeze_flag"].mean())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", type=float, default=0.22, help="目标 unfreeze 率")
    ap.add_argument("--lo", type=float, default=0.005)
    ap.add_argument("--hi", type=float, default=0.011)
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--n", type=int, default=80, help="扫描时的用户数（快速评估）")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    base = G.load_params()
    seed = base["random_seed"]
    n_months = base["data"]["n_months"]

    grid = [round(args.lo + (args.hi - args.lo) * i / args.steps, 5)
            for i in range(args.steps + 1)]
    best = None
    print(f"目标 unfreeze 率 = {args.target:.2f}；快速评估用户数 = {args.n}")
    print(f"{'ratio':>9}  {'unfreeze_rate':>14}")
    for r in grid:
        rate = evaluate(r, args.n, n_months, seed)
        mark = ""
        if best is None or abs(rate - args.target) < abs(best[1] - args.target):
            best = (r, rate)
            mark = "   <== 目前最接近"
        print(f"{r:>9.5f}  {rate:>14.4f}{mark}")

    print(f"\n建议 data.daily_spending_base_ratio = {best[0]:.5f}  (unfreeze_rate = {best[1]:.4f})")
    if args.apply:
        base["data"]["daily_spending_base_ratio"] = best[0]
        with open(os.path.join(ROOT, "params.json"), "w", encoding="utf-8") as f:
            json.dump(base, f, ensure_ascii=False, indent=2)
        print("[apply] 已写回 params.json。请重跑全链路。")
    else:
        print("（加 --apply 可自动写回 params.json）")


if __name__ == "__main__":
    main()
