"""
工银薪e安 - 数据流水线 pipeline.py
合成数据回放，非精度承诺。

函数链: load -> dedup -> recalculate_balance -> fill_missing -> detect_outlier -> window_by_payday -> build_features -> anonymize
输入: data/transactions.csv  (balance_after 由 gen_data.py 统一计算)
输出: data/features.parquet + docs/features.md

关键修正:
  - recalculate_balance: 期初余额从 params.data.initial_balance_months 读取（与 gen_data 一致）
  - unfreeze_flag: 阈值从 params.model.calibrate.unfreeze_threshold_ratio 读取
  - end_balance: 直接取窗口末笔余额，删除 end_balance_recalc
  - balance_chain_ok: 新增窗口内余额链自洽自查列
"""
import os
import json
import hashlib
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_params():
    with open(os.path.join(ROOT, "params.json"), "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------- Step 1: load ----------------
def load(data_path=None):
    if data_path is None:
        data_path = os.path.join(ROOT, "data", "transactions.csv")
    df = pd.read_csv(data_path, encoding="utf-8-sig")
    df["date"] = pd.to_datetime(df["date"])
    # stable sort 保留同日内的生成顺序(salary->rent->credit->spending->large_expense)
    df = df.sort_values(["user_id", "date"], kind="stable").reset_index(drop=True)
    return df


# ---------------- Step 1b: recalculate_balance ----------------
def recalculate_balance(df):
    """按 (user_id, date) 顺序统一重算余额链。

    gen_data.py 已输出正确的 balance_after，本函数作为防御性重算，
    确保与日期顺序永久自洽。期初余额从 params 读取。
    """
    params = load_params()
    init_months = params["data"].get("initial_balance_months", 1.5)

    users_path = os.path.join(ROOT, "data", "users.csv")
    if not os.path.exists(users_path):
        raise FileNotFoundError(f"users.csv not found at {users_path}, cannot recalculate balance")
    users_df = pd.read_csv(users_path, encoding="utf-8-sig")
    salary_map = dict(zip(users_df["user_id"], users_df["salary"]))

    # 稳定排序保留同日内生成顺序
    df = df.sort_values(["user_id", "date"], kind="stable").reset_index(drop=True)

    new_balance = pd.Series(np.nan, index=df.index, dtype=float)
    for uid, g in df.groupby("user_id", sort=False):
        salary = salary_map.get(uid, 0.0)
        init_bal = salary * init_months
        cum = init_bal + g["amount"].cumsum()
        new_balance.loc[g.index] = cum.values

    df["balance_after"] = new_balance.round(2)
    n_users = df["user_id"].nunique()
    n_neg = int((df["balance_after"] < 0).sum())
    print(f"[recalculate_balance] recalculated for {n_users} users, {n_neg} negative balance rows")
    return df


# ---------------- Step 2: dedup ----------------
def dedup(df, keys=None):
    keys = keys or ["user_id", "date", "amount", "type"]
    before = len(df)
    df = df.drop_duplicates(subset=keys, keep="first").reset_index(drop=True)
    print(f"[dedup] {before} -> {len(df)} rows")
    return df


# ---------------- Step 3: fill_missing ----------------
def fill_missing(df):
    """区分'无流水'与'流水为零'；补齐无交易日，余额前向填充"""
    all_dates = pd.date_range(df["date"].min(), df["date"].max(), freq="D")
    frames = []
    for uid, g in df.groupby("user_id"):
        # 同一天可能有多笔交易，先按日期聚合：金额求和，余额取最后一笔
        g = g.sort_values("date", kind="stable")
        daily = g.groupby("date").agg(
            amount=("amount", "sum"),
            type=("type", lambda x: ",".join(x)),
            balance_after=("balance_after", "last"),
        )
        # 补齐无交易日
        daily = daily.reindex(all_dates)
        daily["user_id"] = uid
        daily["amount"] = daily["amount"].fillna(0.0)
        daily["type"] = daily["type"].fillna("none")
        daily["has_tx"] = daily["type"] != "none"
        # 余额前向填充
        daily["balance_after"] = daily["balance_after"].ffill()
        daily = daily.reset_index().rename(columns={"index": "date"})
        frames.append(daily)
    out = pd.concat(frames, ignore_index=True)
    print(f"[fill_missing] {len(df)} -> {len(out)} rows")
    return out


# ---------------- Step 4: detect_outlier ----------------
def detect_outlier(df):
    """3σ 与 IQR 双阈值交叉判定，标记不删除，偶发大额转特征"""
    df["is_outlier_3sigma"] = False
    df["is_outlier_iqr"] = False
    df["is_outlier"] = False

    for uid, g in df.groupby("user_id"):
        amt = g["amount"].abs()
        mean, std = amt.mean(), amt.std()
        if std > 0:
            sigma_mask = (amt - mean).abs() > 3 * std
        else:
            sigma_mask = pd.Series(False, index=g.index)

        q1, q3 = amt.quantile(0.25), amt.quantile(0.75)
        iqr = q3 - q1
        iqr_mask = amt > q3 + 1.5 * iqr

        df.loc[g.index, "is_outlier_3sigma"] = sigma_mask.values
        df.loc[g.index, "is_outlier_iqr"] = iqr_mask.values
        # 双阈值交叉判定：两个都命中才算异常
        df.loc[g.index, "is_outlier"] = (sigma_mask & iqr_mask).values

    n_out = df["is_outlier"].sum()
    print(f"[detect_outlier] {n_out} outliers marked (not deleted)")
    return df


# ---------------- Step 5: window_by_payday ----------------
def detect_payday_for_user(g):
    """自动识别发薪日：取 type 含 salary 的日期的日号众数"""
    salary = g[g["type"].str.contains("salary", na=False)]
    if len(salary) == 0:
        # 兜底：取大额正收入的众数日
        pos = g[g["amount"] > 0]
        if len(pos) == 0:
            return 15
        return int(pos["date"].dt.day.mode().iloc[0])
    return int(salary["date"].dt.day.mode().iloc[0])


def window_by_payday(df):
    """按发薪日切窗 [payday, next_payday)，窗口长度作为特征"""
    windows = []
    for uid, g in df.groupby("user_id"):
        payday = detect_payday_for_user(g)
        g = g.sort_values("date", kind="stable").reset_index(drop=True)

        # 找到所有发薪日锚点日期
        payday_dates = []
        for _, row in g[g["type"].str.contains("salary", na=False)].iterrows():
            payday_dates.append(row["date"])
        if not payday_dates:
            continue

        # 生成窗口
        for i in range(len(payday_dates) - 1):
            w_start = payday_dates[i]
            w_end = payday_dates[i + 1]
            win = g[(g["date"] >= w_start) & (g["date"] < w_end)].copy()
            if len(win) == 0:
                continue
            windows.append({
                "user_id": uid,
                "window_id": f"{uid}_w{i+1:03d}",
                "payday": payday,
                "w_start": w_start,
                "w_end": w_end,
                "period_days": (w_end - w_start).days,
                "tx_df": win,
            })
    print(f"[window_by_payday] {len(windows)} windows")
    return windows


# ---------------- Step 6: build_features ----------------
def build_features(windows, params=None):
    """四类特征：周期锚点 / 刚性支出 / 波动 / 自校准"""
    if params is None:
        params = load_params()

    rows = []
    # 先按用户、窗口顺序排序以便取上一周期
    by_user = {}
    for w in windows:
        by_user.setdefault(w["user_id"], []).append(w)
    for uid in by_user:
        by_user[uid].sort(key=lambda x: x["w_start"])

    for uid, wlist in by_user.items():
        for i, w in enumerate(wlist):
            g = w["tx_df"]
            spending = g[g["amount"] < 0]["amount"].abs()
            income = g[g["amount"] > 0]["amount"]
            salary_amt = income[g["type"].str.contains("salary", na=False)].sum() if len(g[g["type"].str.contains("salary", na=False)]) > 0 else 0

            # --- 周期锚点类 ---
            period_days = w["period_days"]
            days_since_payday = 0  # 窗口起点即发薪日
            prev_end_balance = wlist[i - 1]["tx_df"]["balance_after"].iloc[-1] if i > 0 else g["balance_after"].iloc[0]

            # --- 刚性支出类 ---
            rent = g[g["type"].str.contains("rent", na=False)]["amount"].abs().sum()
            credit = g[g["type"].str.contains("credit_repayment", na=False)]["amount"].abs().sum()
            fixed_expense_total = rent + credit
            fixed_expense_ratio = fixed_expense_total / salary_amt if salary_amt > 0 else 0
            repayment_days = ",".join(str(d) for d in sorted(g[g["type"].str.contains("rent|credit_repayment", na=False)]["date"].dt.day.unique()))

            # --- 波动类 ---
            spend_mean = spending.mean() if len(spending) > 0 else 0
            spend_var = spending.var() if len(spending) > 1 else 0
            weekend_mask = g["date"].dt.weekday >= 5
            weekend_spend = g[weekend_mask & (g["amount"] < 0)]["amount"].abs().sum()
            total_spend = spending.sum()
            weekend_spend_ratio = weekend_spend / total_spend if total_spend > 0 else 0
            tx_count = len(g[g["type"] != "none"])  # none = 无交易日, 其他都有交易

            # --- 自校准类（解冻）---
            # 解冻定义：可支配余额跌破阈值即记 unfreeze_flag=1。
            # 阈值 = 本窗口工资 × params["model"]["calibrate"]["unfreeze_threshold_ratio"]
            #        （默认 0.0 → 余额为负 = 真实透支 = 钱不够花）
            # 语义：钱不够花 → 用户被迫动用锁定资金 → 校准层上调 α 的信号源
            thr_ratio = float(params.get("model", {}).get("calibrate", {})
                              .get("unfreeze_threshold_ratio", 0.0))
            threshold = salary_amt * thr_ratio
            bal = g["balance_after"]
            below = bal < threshold
            if below.any():
                unfreeze_idx = below.idxmax()  # 首次跌破
                unfreeze_flag = 1
                unfreeze_amount = threshold - bal.loc[unfreeze_idx]
                unfreeze_day = (g.loc[unfreeze_idx, "date"] - w["w_start"]).days
            else:
                unfreeze_flag = 0
                unfreeze_amount = 0.0
                unfreeze_day = -1

            # 余额链自查：窗口内逐笔累计是否与 balance_after 一致
            # 反推初始余额 = balance_after[0] - amount[0]，再全程 cumsum 比较
            # 避免 round(2) 累积误差导致逐笔校验误判
            est_init = g["balance_after"].iloc[0] - g["amount"].iloc[0]
            balance_chain_ok = bool(len(g) < 2 or np.allclose(
                est_init + g["amount"].cumsum().values,
                g["balance_after"].values,
                atol=0.02,
            ))

            rows.append({
                "user_id": uid,
                "window_id": w["window_id"],
                "period_days": period_days,
                "days_since_payday": days_since_payday,
                "prev_end_balance": prev_end_balance,
                "salary": salary_amt,
                "fixed_expense_total": round(fixed_expense_total, 2),
                "fixed_expense_ratio": round(fixed_expense_ratio, 4),
                "repayment_days": repayment_days,
                "spend_mean": round(spend_mean, 2),
                "spend_var": round(spend_var, 2),
                "weekend_spend_ratio": round(weekend_spend_ratio, 4),
                "tx_count": tx_count,
                "unfreeze_flag": unfreeze_flag,
                "unfreeze_amount": round(unfreeze_amount, 2),
                "unfreeze_day": unfreeze_day,
                "end_balance": round(g["balance_after"].iloc[-1], 2),
                "balance_chain_ok": balance_chain_ok,
            })

    feats = pd.DataFrame(rows)
    # 上周期解冻特征（自校准类用上周期值）
    feats = feats.sort_values(["user_id", "window_id"]).reset_index(drop=True)
    feats["prev_unfreeze_flag"] = feats.groupby("user_id")["unfreeze_flag"].shift(1).fillna(0).astype(int)
    feats["prev_unfreeze_amount"] = feats.groupby("user_id")["unfreeze_amount"].shift(1).fillna(0.0)
    feats["prev_unfreeze_day"] = feats.groupby("user_id")["unfreeze_day"].shift(1).fillna(-1).astype(int)
    print(f"[build_features] {len(feats)} feature rows, {len(feats.columns)} cols")
    return feats


# ---------------- Step 7: anonymize ----------------
def anonymize(feats):
    """四层脱敏：主键哈希 -> 数值分箱 -> 金额比率化 -> 时序差分"""
    df = feats.copy()

    # 1. 主键哈希化
    df["user_id"] = df["user_id"].apply(lambda x: hashlib.sha256(x.encode()).hexdigest()[:16])
    df["window_id"] = df["window_id"].apply(lambda x: hashlib.sha256(x.encode()).hexdigest()[:16])

    # 2. 数值分箱（工资）
    salary_bins = [0, 4000, 8000, 12000, 20000]
    salary_labels = [1, 2, 3, 4]
    df["salary_bin"] = pd.cut(df["salary"], bins=salary_bins, labels=salary_labels, include_lowest=True).astype(int)

    # 3. 金额比率化（转占收入比）
    # 注意: salary 可能为 0 (无发薪的窗口)，需将 inf 和 NaN 都替换为 0
    df["end_balance_ratio"] = (df["end_balance"] / df["salary"]).replace([np.inf, -np.inf], 0).fillna(0).round(4)
    df["prev_end_balance_ratio"] = (df["prev_end_balance"] / df["salary"]).replace([np.inf, -np.inf], 0).fillna(0).round(4)
    df["unfreeze_amount_ratio"] = (df["unfreeze_amount"] / df["salary"]).replace([np.inf, -np.inf], 0).fillna(0).round(4)
    df["prev_unfreeze_amount_ratio"] = (df["prev_unfreeze_amount"] / df["salary"]).replace([np.inf, -np.inf], 0).fillna(0).round(4)

    # 4. 时序差分（余额差分）
    df = df.sort_values(["user_id", "window_id"]).reset_index(drop=True)
    df["balance_diff"] = df.groupby("user_id")["end_balance_ratio"].diff().fillna(0).round(4)

    # salary 重命名为 salary_amt 保留（便于验收核对窗口工资），最终导出时再删
    if "salary" in df.columns:
        df = df.rename(columns={"salary": "salary_amt"})
    drop_cols = ["end_balance", "prev_end_balance",
                 "unfreeze_amount", "prev_unfreeze_amount", "spend_mean", "spend_var"]
    df = df.drop(columns=[c for c in drop_cols if c in df.columns])

    df["anonymized"] = True
    print(f"[anonymize] {len(df)} rows, {len(df.columns)} cols (anonymized)")
    return df


# ---------------- features.md ----------------
def write_features_md(feats):
    docs_dir = os.path.join(ROOT, "docs")
    os.makedirs(docs_dir, exist_ok=True)
    md_path = os.path.join(docs_dir, "features.md")

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

    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[features.md] written to {md_path}")


# ---------------- main ----------------
def run_pipeline():
    params = load_params()
    df = load()
    df = dedup(df, params["pipeline"]["dedup_keys"])
    # 在 dedup 之后重算余额链（防御性重算，与 gen_data 口径一致）
    df = recalculate_balance(df)

    # 【本轮修改】窗口与特征一律基于"原始单笔流水"，不再基于日聚合表。
    # 原因：fill_missing 会把同一天的多笔交易 agg 成一行
    #       （amount=当日净额、type="salary,rent,spending"），
    #       而 build_features 是按"单笔金额"去解释 amount 的，
    #       导致 salary_amt 被算成发薪日净额 → fixed_expense_ratio /
    #       end_balance_ratio(LightGBM 标签) 全部失真。
    windows = window_by_payday(df)
    feats = build_features(windows, params)

    # 日聚合表只用于辅助（余额曲线 / 无交易日统计），不参与特征计算
    daily = detect_outlier(fill_missing(df))
    _sanity_check_features(feats, params)

    feats_anon = anonymize(feats)
    # 注意：parquet 和 features.md 写入由根目录脚本负责
    return feats_anon


def _sanity_check_features(feats, params):
    """特征口径自检：口径错了就直接报错，不许静默继续。"""
    problems = []

    # 1) 固定支出占比不可能 > 1（房租<=40% + 还款<=15%）
    if "fixed_expense_ratio" in feats.columns:
        mx = float(feats["fixed_expense_ratio"].max())
        if mx > 1.5:
            problems.append(
                f"fixed_expense_ratio 最大值 = {mx:.4f}（不可能 > 1.5）。"
                "说明 salary_amt 取到的不是月薪，而是发薪日净额 —— "
                "检查 window_by_payday 的输入是不是 fill_missing 之后的日聚合表。"
            )

    # 2) 期末余额/收入 落在合理区间
    if "end_balance_ratio" in feats.columns:
        mn, mx = float(feats["end_balance_ratio"].min()), float(feats["end_balance_ratio"].max())
        if mn < -10 or mx > 10:
            problems.append(
                f"end_balance_ratio 取值范围 [{mn:.4f}, {mx:.4f}]，超出 [-10, 10]。"
                "同因：分母 salary 失真。"
            )

    # 3) 每窗口的工资应等于该用户月薪
    users = pd.read_csv(os.path.join(ROOT, "data", "users.csv"), encoding="utf-8-sig")
    salary_map = dict(zip(users["user_id"], users["salary"]))
    tx = pd.read_csv(os.path.join(ROOT, "data", "transactions.csv"), encoding="utf-8-sig")
    tx["date"] = pd.to_datetime(tx["date"])
    bad = 0
    checked = 0
    for uid, g in tx.groupby("user_id", sort=False):
        g = g.sort_values("date", kind="stable")
        anchors = g.loc[g["type"] == "salary", "date"].tolist()
        for i in range(len(anchors) - 1):
            a, b = anchors[i], anchors[i + 1]
            win = g[(g["date"] >= a) & (g["date"] < b)]
            sal = float(win.loc[win["type"] == "salary", "amount"].sum())
            checked += 1
            if abs(sal - float(salary_map.get(uid, 0))) > 1.0:
                bad += 1
    if bad > 0:
        problems.append(f"有 {bad}/{checked} 个窗口的工资合计 ≠ 该用户月薪（应为 0）")

    print(f"[sanity] 特征口径自检: fixed_expense_ratio.max="
          f"{feats['fixed_expense_ratio'].max() if 'fixed_expense_ratio' in feats else 'NA'}, "
          f"发薪窗口工资核对 {checked - bad}/{checked} 通过")

    if problems:
        raise RuntimeError("特征口径自检未通过：\n  - " + "\n  - ".join(problems))


if __name__ == "__main__":
    run_pipeline()
