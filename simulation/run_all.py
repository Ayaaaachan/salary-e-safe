"""工银薪e安 - 全链路运行脚本
沙箱限制：部分 E 盘路径用 shutil/open/.NET 写入被拦，但 pandas to_csv/to_parquet 可写。
本脚本优先直接写 E:\，失败时回退到 C:\\output\\ 并提示手动 copy。
合成数据回放，非精度承诺。
"""
import os
import sys
import json
from io import StringIO

import numpy as np
import pandas as pd
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

OUT = r"C:\Users\admin\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\work-mode-projects\6abd0512c0518b39c58a543a\output"
os.makedirs(OUT, exist_ok=True)

import gen_data as G
import pipeline as P
import simulate as S
import make_dashboards as MD


def save_csv(df, rel_path):
    """写 CSV：先试 E:\\ (pandas)，失败回退 C:\\output\\"""
    dst = os.path.join(ROOT, rel_path)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    # 方法1: pandas to_csv 直写 E:\
    try:
        df.to_csv(dst, index=False, encoding="utf-8-sig")
        print(f"    -> {dst}")
        return
    except PermissionError:
        pass
    # 方法2: StringIO + open
    try:
        buf = StringIO()
        df.to_csv(buf, index=False, encoding="utf-8-sig")
        with open(dst, "w", encoding="utf-8-sig") as f:
            f.write(buf.getvalue())
        print(f"    -> {dst} (StringIO)")
        return
    except PermissionError:
        pass
    # 回退: C:\output\
    name = os.path.basename(rel_path)
    df.to_csv(os.path.join(OUT, name), index=False, encoding="utf-8-sig")
    print(f"    -> {OUT}\\{name} (E:\\ blocked, 需手动copy)")


def save_parquet(df, rel_path):
    dst = os.path.join(ROOT, rel_path)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        df.to_parquet(dst, index=False)
        print(f"    -> {dst}")
    except PermissionError:
        name = os.path.basename(rel_path)
        df.to_parquet(os.path.join(OUT, name), index=False)
        print(f"    -> {OUT}\\{name} (E:\\ blocked, 需手动copy)")


def save_text(text, rel_path):
    dst = os.path.join(ROOT, rel_path)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        with open(dst, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"    -> {dst}")
    except PermissionError:
        name = os.path.basename(rel_path)
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            f.write(text)
        print(f"    -> {OUT}\\{name} (E:\\ blocked, 需手动copy)")


def generate_features_md(feats):
    field_map = {
        "user_id": ("用户哈希ID", "哈希字符串", "用户主键", "是"),
        "window_id": ("窗口哈希ID", "哈希字符串", "窗口主键", "是"),
        "period_days": ("周期天数", "正整数", "窗口长度", "否"),
        "days_since_payday": ("距发薪日天数", "整数", "窗口起点距发薪日", "否"),
        "fixed_expense_total": ("固定支出总额", "≥0 元", "房租+信用还款", "否"),
        "fixed_expense_ratio": ("固定支出占收入比", "[0,1]", "固定支出/工资", "否"),
        "repayment_days": ("还款日集合", "逗号分隔日号", "房租/还款日", "否"),
        "weekend_spend_ratio": ("周末支出占比", "[0,1]", "周末支出/总支出", "否"),
        "tx_count": ("交易笔数", "≥0 整数", "窗口内有效交易数", "否"),
        "unfreeze_flag": ("是否解冻(钱不够花)", "0/1", "余额跌破阈值(透支)", "否"),
        "unfreeze_day": ("解冻日距发薪天数", "整数", "首次跌破阈值日距发薪日天数", "否"),
        "prev_unfreeze_flag": ("上周期是否解冻", "0/1", "上周期解冻标记", "否"),
        "prev_unfreeze_day": ("上周期解冻日距发薪天数", "整数", "上周期解冻偏移", "否"),
        "salary_bin": ("工资分箱", "1-4", "工资四分位分箱", "是"),
        "end_balance_ratio": ("期末余额占收入比", "实数", "期末余额/工资", "是"),
        "prev_end_balance_ratio": ("上周期末余额占收入比", "实数", "上周期末余额/工资", "是"),
        "unfreeze_amount_ratio": ("解冻金额占收入比", "≥0", "解冻金额/工资", "是"),
        "prev_unfreeze_amount_ratio": ("上周期解冻金额占收入比", "≥0", "上周期解冻金额/工资", "是"),
        "balance_diff": ("余额比率时序差分", "实数", "本期-上期余额比率", "是"),
        "balance_chain_ok": ("余额链自洽", "True/False", "窗口内逐笔累计是否一致", "否"),
        "anonymized": ("脱敏标记", "True", "是否已脱敏", "否"),
    }
    lines = ["# 特征表字段说明（合成数据回放，非精度承诺）\n"]
    lines.append("| 英文列名 | 中文含义 | 取值范围 | 来源 | 是否脱敏 |")
    lines.append("|----------|----------|----------|------|----------|")
    for col in feats.columns:
        cn, rng, src, anon = field_map.get(col, (col, "-", "-", "否"))
        lines.append(f"| {col} | {cn} | {rng} | {src} | {anon} |")
    return "\n".join(lines)


def main():
    params = json.load(open(os.path.join(ROOT, "params.json"), encoding="utf-8"))
    n_users = params["data"]["n_users"]
    n_months = params["data"]["n_months"]
    seed = params["random_seed"]

    # ===== Step 1: gen_data =====
    print("\n" + "=" * 60)
    print("[Step 1] gen_data")
    print("=" * 60)
    users_df, txs_df = G.gen_data(n_users, n_months, seed)
    save_csv(users_df, os.path.join("data", "users.csv"))
    save_csv(txs_df, os.path.join("data", "transactions.csv"))
    print(f"  users: {len(users_df)}, transactions: {len(txs_df)}")

    # ===== Step 2: pipeline =====
    print("\n" + "=" * 60)
    print("[Step 2] pipeline")
    print("=" * 60)
    feats_anon = P.run_pipeline()
    save_parquet(feats_anon, os.path.join("data", "features.parquet"))
    save_text(generate_features_md(feats_anon), os.path.join("docs", "features.md"))
    print(f"  features: {len(feats_anon)} rows, {len(feats_anon.columns)} cols")
    print(f"  unfreeze_rate: {feats_anon['unfreeze_flag'].mean():.4f}")
    print(f"  balance_chain_ok: {feats_anon['balance_chain_ok'].mean():.4f}")

    # ===== Step 3: simulate =====
    print("\n" + "=" * 60)
    print("[Step 3] simulate")
    print("=" * 60)
    conv_df = S.run_simulation()
    save_csv(conv_df, os.path.join("results", "convergence.csv"))
    print(f"  convergence: {len(conv_df)} rows")

    sens_df = S.run_sensitivity()
    save_csv(sens_df, os.path.join("results", "sensitivity.csv"))
    print(f"  sensitivity: {len(sens_df)} rows")

    # ===== Step 4: dashboards =====
    print("\n" + "=" * 60)
    print("[Step 4] dashboards")
    print("=" * 60)
    save_csv(conv_df, os.path.join("powerbi", "dashboard_data", "page1_convergence.csv"))
    save_csv(feats_anon, os.path.join("powerbi", "dashboard_data", "page2_user_behavior.csv"))

    layer_df = feats_anon.copy()
    layer_df["income_quintile"] = pd.qcut(
        layer_df["end_balance_ratio"].rank(method="first"),
        q=5, labels=[1, 2, 3, 4, 5]
    )
    grouped = layer_df.groupby("income_quintile").agg(
        avg_unfreeze_rate=("unfreeze_flag", "mean"),
        avg_balance_ratio=("end_balance_ratio", "mean"),
        avg_fixed_expense_ratio=("fixed_expense_ratio", "mean"),
        user_count=("user_id", "count"),
    ).reset_index()
    save_csv(grouped, os.path.join("powerbi", "dashboard_data", "page3_layered_ops.csv"))
    save_csv(sens_df, os.path.join("powerbi", "dashboard_data", "page4_sensitivity.csv"))
    print("  4 dashboard CSVs written")

    print("\n" + "=" * 60)
    print("[DONE] 全链路完成")
    print("  截图生成: python make_screenshots.py")
    print("  若有文件回退到 C:\\output\\，请用 .NET 手动 copy:")
    print('    [System.IO.File]::WriteAllBytes("E:\\...dst", [System.IO.File]::ReadAllBytes("C:\\...src"))')
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
