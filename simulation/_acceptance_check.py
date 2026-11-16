"""工银薪e安 - 全链路验收自查（合成数据回放，非精度承诺）"""
import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

print("=" * 66)
print("工银薪e安 验收自查报告")
print("=" * 66)

# --- 1. 参数 ---
p = json.load(open(os.path.join(ROOT, "params.json"), encoding="utf-8"))
d = p["data"]
print(f"\n[参数] daily_spending_base_ratio = {d['daily_spending_base_ratio']}")
print(f"[参数] initial_balance_months    = {d['initial_balance_months']}")
print(f"[参数] unfreeze_threshold_ratio  = {p['model']['calibrate']['unfreeze_threshold_ratio']}")
print(f"[参数] predict.test_size         = {p['model']['predict'].get('test_size', '缺失!')}")
print(f"[参数] 是否残留 expected_convergence: "
      f"{'是（必须删）' if 'expected_convergence' in json.dumps(p) else '否 ✓'}")

# --- 2. 特征表 ---
f = pd.read_parquet(os.path.join(ROOT, "data", "features.parquet"))
rate = float(f["unfreeze_flag"].mean())
print(f"\n[信号] unfreeze_flag 均值      = {rate:.4f}   (必须 0.15~0.30)")
print(f"[信号] 非零 unfreeze 窗口数    = {int(f['unfreeze_flag'].sum())}/{len(f)}")
print(f"[信号] prev_unfreeze_flag 均值 = {float(f['prev_unfreeze_flag'].mean()):.4f}")
print(f"[信号] unfreeze_amount_ratio 最大值 = {float(f['unfreeze_amount_ratio'].max()):.4f}")
if "balance_chain_ok" in f.columns:
    print(f"[信号] 窗口余额链自洽         = {int(f['balance_chain_ok'].sum())}/{len(f)}")
else:
    print("[信号] balance_chain_ok 列缺失（3.3(c) 未做）")

# --- 2b. 特征口径自检（本轮新增） ---
print("\n[口径] 特征表金额字段合理性")
feats = f
for col, lo, hi in [("fixed_expense_ratio", 0.0, 1.5),
                    ("end_balance_ratio", -10.0, 10.0),
                    ("weekend_spend_ratio", 0.0, 1.0)]:
    if col in feats.columns:
        mn, mx = float(feats[col].min()), float(feats[col].max())
        ok = (lo <= mn) and (mx <= hi)
        print(f"  {'OK ' if ok else '!! '}{col:<22} [{mn:.4f}, {mx:.4f}]  期望 ⊆ [{lo}, {hi}]")
    else:
        print(f"  !! {col} 列缺失")

# 每窗口工资应等于该用户月薪
users = pd.read_csv(os.path.join(ROOT, "data", "users.csv"), encoding="utf-8-sig")
smap = dict(zip(users["user_id"], users["salary"]))
tx2 = pd.read_csv(os.path.join(ROOT, "data", "transactions.csv"), encoding="utf-8-sig")
tx2["date"] = pd.to_datetime(tx2["date"])
bad = tot = 0
for uid, g in tx2.groupby("user_id", sort=False):
    g = g.sort_values("date", kind="stable")
    anch = g.loc[g["type"] == "salary", "date"].tolist()
    for i in range(len(anch) - 1):
        win = g[(g["date"] >= anch[i]) & (g["date"] < anch[i + 1])]
        sal = float(win.loc[win["type"] == "salary", "amount"].sum())
        tot += 1
        if abs(sal - float(smap.get(uid, 0))) > 1.0:
            bad += 1
print(f"  {'OK ' if bad == 0 else '!! '}窗口工资核对: {tot - bad}/{tot} 通过（期望全部通过）")

# tx_count 应是笔数（60~80），不是天数（28~31）
if "tx_count" in feats.columns:
    med = float(feats["tx_count"].median())
    print(f"  {'OK ' if med > 45 else '!! '}tx_count 中位数 = {med:.1f}"
          f"（应 > 45；若约 31 说明仍在用日聚合表）")

# --- 3. 收敛表 ---
c = pd.read_csv(os.path.join(ROOT, "results", "convergence.csv"))
print(f"\n[收敛] 列名: {list(c.columns)}")
for col in ["mape", "raw_mape", "gap", "tight_signal_ratio", "alpha", "n_windows"]:
    if col not in c.columns:
        print(f"  !! 缺列 {col}")
print(f"[收敛] raw_mape  : {[round(v, 4) for v in c['raw_mape']]}")
print(f"[收敛] adjusted  : {[round(v, 4) for v in c['mape']]}")
print(f"[收敛] gap       : {[round(v, 4) for v in c['gap']]}")
print(f"[收敛] 偏紧占比  : {[round(v, 4) for v in c['tight_signal_ratio']]}")
print(f"[收敛] alpha     : {[round(v, 4) for v in c['alpha']]}")

# --- 4. 关键判断 ---
alpha = list(c["alpha"].values)
spread = max(alpha) - min(alpha)
print(f"\n[判断] alpha 极差 = {spread:.4f}")
if spread < 0.005:
    print("       !! alpha 几乎不动 —— 信号可能仍是死的")
else:
    print("       ✓ alpha 有明显变化")

# --- 5. 产品图 ---
print("\n[图] powerbi/screenshots/")
for n in ["page1_convergence.png", "page2_user_behavior.png",
          "page3_layered_ops.png", "page4_sensitivity.png"]:
    fp = os.path.join(ROOT, "powerbi", "screenshots", n)
    print(f"  {'OK ' if os.path.exists(fp) else '缺 '} {n}")

# --- 6. 文档是否同步 ---
print("\n[文档] 是否仍含旧数字（命中即为未同步）")
OLD = ["41.70", "39.23", "37.29", "39.87", "0.860", "0.9819", "63.4", "51.82"]
for rel in ["docs/ppt_outline.md", "docs/video_script.md", "results/summary.md"]:
    fp = os.path.join(ROOT, rel)
    if not os.path.exists(fp):
        print(f"  {rel}: 不存在")
        continue
    t = open(fp, encoding="utf-8").read()
    hits = [s for s in OLD if s in t]
    print(f"  {rel}: {'命中旧数字 ' + str(hits) if hits else '干净 ✓'}")

print("\n" + "=" * 66)
print("请把以上完整输出发回。")
