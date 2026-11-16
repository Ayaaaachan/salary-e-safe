"""
工银薪e安 - 三层架构 layers.py
合成数据回放，非精度承诺。

C-1 predict:  预测层（基线），LightGBM 分位数回归，输出区间 + naive 基线对照
C-2 calibrate: 校准层（核心），利用 unfreeze 信号做非对称修正，输出 alpha 序列
C-3 guard:     护栏层，规则引擎正则黑名单，不用机器学习
"""
import os
import re
import json
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_params():
    with open(os.path.join(ROOT, "params.json"), "r", encoding="utf-8") as f:
        return json.load(f)


# ================================================================
# C-1 预测层
# ================================================================
def predict(features_df, params, period_idx):
    """
    预测层: LightGBM 分位数回归 + naive 基线对照。
    直接使用全部特征行(prev_*列已编码历史信息)，不做滑动窗口。

    P1-3 修正: 使用固定 80/20 划分（同一测试集），
    使得各周期 MAPE 可比。改善应来自 calibrate 层 alpha，而非训练集扩张。

    Returns:
        pred_q10, pred_q50, pred_q90: 预测区间下/中/上界(测试集)
        y_test: 测试集真实值
        naive_pred: naive 基线(测试集)
        mape: 预测 MAPE(测试集)
        naive_mape: naive 基线 MAPE
    """
    import lightgbm as lgb
    from sklearn.model_selection import train_test_split
    # 兼容 lightgbm 4.5 + sklearn 1.9
    from sklearn.utils import validation as _skv
    _orig_cxy = _skv.check_X_y
    def _safe_cxy(*a, **kw):
        kw.pop("force_all_finite", None)
        return _orig_cxy(*a, **kw)
    _skv.check_X_y = _safe_cxy
    lgb.sklearn._LGBMCheckXY = _safe_cxy
    _orig_ca = _skv.check_array
    def _safe_ca(*a, **kw):
        kw.pop("force_all_finite", None)
        return _orig_ca(*a, **kw)
    _skv.check_array = _safe_ca
    lgb.sklearn._LGBMCheckArray = _safe_ca

    model_cfg = params["model"]["predict"]
    quantiles = model_cfg["quantiles"]

    # 特征列: 排除标签、标识列和非数值列
    exclude = {"user_id", "window_id", "end_balance_ratio", "anonymized", "repayment_days", "salary_amt"}
    # 第1周期不含解冻特征(模拟尚未纳入解冻信号)
    unfreeze_cols = {"unfreeze_flag", "unfreeze_day", "unfreeze_amount_ratio",
                     "prev_unfreeze_flag", "prev_unfreeze_day", "prev_unfreeze_amount_ratio"}
    if period_idx == 0:
        exclude = exclude | unfreeze_cols

    feat_cols = [c for c in features_df.columns if c not in exclude
                 and pd.api.types.is_numeric_dtype(features_df[c])]
    label_col = "end_balance_ratio"

    df = features_df.copy()
    X = df[feat_cols].values
    y = df[label_col].values

    if len(X) < 10:
        naive_pred = np.full(len(y), np.mean(y) if len(y) > 0 else 0)
        mape = _safe_mape(y, naive_pred)
        return naive_pred * 0.9, naive_pred, naive_pred * 1.1, y, naive_pred, mape, mape

    # ---- 训练/测试分割: 固定测试集，保证各周期 MAPE 可比 ----
    # 【本轮修改】原 train_ratios=[0.6,0.7,0.8,0.9] 会让每个周期换一个测试集，
    # 导致 P1~P4 的 MAPE 在不同测试集上计算、互相不可比。
    # 现在固定 test_size + 固定随机种子，四个周期共用同一个测试集；
    # 周期之间的差异只来自 α 校准与特征集（第 1 周期不含解冻特征）。
    test_size = float(model_cfg.get("test_size", 0.3))
    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=test_size, random_state=params["random_seed"]
    )

    # ---- naive 基线: 全局均值 ----
    naive_pred = np.full(len(y_te), np.mean(y_tr))
    naive_mape = _safe_mape(y_te, naive_pred)

    # ---- LightGBM 分位数回归 ----
    preds = {}
    for q in quantiles:
        model = lgb.LGBMRegressor(
            objective="quantile",
            alpha=q,
            n_estimators=100,
            max_depth=4,
            learning_rate=0.1,
            random_state=params["random_seed"],
            verbose=-1,
        )
        model.fit(X_tr, y_tr)
        preds[q] = model.predict(X_te)

    pred_q10 = preds[quantiles[0]]
    pred_q50 = preds[quantiles[1]]
    pred_q90 = preds[quantiles[2]]

    mape = _safe_mape(y_te, pred_q50)

    return pred_q10, pred_q50, pred_q90, y_te, naive_pred, mape, naive_mape


def _safe_mape(y_true, y_pred, floor=0.2):
    """安全 MAPE: 分母加 floor 防止小值爆炸(合成数据回放，非精度承诺)"""
    y_true = np.array(y_true, dtype=float)
    y_pred = np.array(y_pred, dtype=float)
    denom = np.maximum(np.abs(y_true), floor)
    return float(np.mean(np.abs(y_true - y_pred) / denom))


# ================================================================
# C-2 校准层
# ================================================================
def calibrate(features_df, params, period_idx, prev_alpha=1.0,
              pred_q50=None, y_test=None):
    """
    校准层(核心): 利用 unfreeze_flag 作为免费偏差标签。
    非对称修正规则: 上调快、下调慢。
    初始步长从 params 读取，每周期衰减。

    新语义(P1-1修正后):
      unfreeze_flag=1 表示余额跌破 0（透支）= 钱不够花 = 模型高估 = 需下调(慢)
      unfreeze_flag=0 表示余额未跌破 = 钱够花 = 模型可能偏紧 = 需上调(快)

    Returns:
        alpha: 本周期修正系数
        tight_signal_ratio: 偏紧信号占比(调整后预测小于真实值的比例)
    """
    cal_cfg = params["model"]["calibrate"]
    initial_step = cal_cfg["initial_step"]
    decay = cal_cfg["decay"]
    up_speed = cal_cfg["asymmetric"]["up_speed"]
    down_speed = cal_cfg["asymmetric"]["down_speed"]

    # 步长衰减
    step = initial_step * (decay ** period_idx)

    # 利用 unfreeze 信号计算偏差方向
    # 新语义: unfreeze_flag=1 = 钱不够花 = 模型高估
    #   unfreeze_rate 高 -> 模型高估 -> bias_signal 小于0 -> 下调(慢)
    #   unfreeze_rate 低 -> 模型偏紧 -> bias_signal 大于0 -> 上调(快)
    if "unfreeze_flag" in features_df.columns and period_idx > 0:
        unfreeze_rate = features_df["unfreeze_flag"].mean()
    else:
        # 第1周期无解冻数据，默认偏紧
        unfreeze_rate = 0.3

    bias_signal = 0.35 - unfreeze_rate  # neutral=0.35, 大于0 模型偏紧(需上调), 小于0 模型高估(需下调)

    # 非对称修正
    if bias_signal > 0:
        # 偏紧 -> 快速上调
        delta = step * up_speed * min(abs(bias_signal) * 4, 1.0)
    else:
        # 高估 -> 缓慢下调
        delta = -step * down_speed * min(abs(bias_signal) * 4, 1.0)

    alpha = prev_alpha + delta
    alpha = max(0.5, min(2.0, alpha))  # 安全边界

    # 偏紧信号占比: 调整后预测小于真实值 = 模型偏紧
    if pred_q50 is not None and y_test is not None:
        adjusted_pred = pred_q50 * alpha
        tight_signal_ratio = float(np.mean(adjusted_pred < y_test))
    else:
        # 无预测数据时从特征估算
        if "prev_unfreeze_flag" in features_df.columns:
            tight_signal_ratio = float((features_df["prev_unfreeze_flag"] == 0).mean())
        else:
            tight_signal_ratio = 0.6  # 初始偏紧

    return alpha, tight_signal_ratio


# ================================================================
# C-3 护栏层
# ================================================================
def guard(text, params=None):
    """
    护栏层: 规则引擎，正则黑名单。
    明确不用机器学习。
    接口: guard(text) -> (passed, hits)

    Returns:
        passed: bool, True=通过(无违规), False=拦截
        hits: list[str], 命中的违规词列表
    """
    if params is None:
        params = load_params()

    blacklist = params["model"]["guard"]["blacklist"]
    hits = []
    for word in blacklist:
        if re.search(word, text):
            hits.append(word)

    passed = len(hits) == 0
    return passed, hits
