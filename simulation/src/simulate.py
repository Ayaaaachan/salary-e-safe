"""
工银薪e安 - 仿真回放主驱动 simulate.py
合成数据回放，非精度承诺。

跑 200 虚拟用户 x 4 周期回放，输出:
  results/convergence.csv  (period, mape, raw_mape, gap, naive_mape, tight_signal_ratio, alpha, n_windows)
  results/sensitivity.csv  (initial_step, decay, final_mape, final_tight_ratio)

说明: alpha 是每周期全局修正系数(标量)，不做逐用户校准。
所有指标由 LightGBM 真实预测 + alpha 校准后计算，不使用期望值贴数。
alpha_init 从 params.json 的 model.calibrate.alpha_init 读取。
"""
import os
import sys
import json
import numpy as np
import pandas as pd

from layers import predict, calibrate, guard, load_params, _safe_mape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def run_simulation():
    params = load_params()
    sim_cfg = params["simulation"]
    n_periods = sim_cfg["n_periods"]
    seed = params["random_seed"]

    # 从 params.json 读取初始 alpha（P1-4 修正：不再硬编码）
    initial_alpha = params["model"]["calibrate"].get("alpha_init", 0.85)

    # 加载特征表
    feat_path = os.path.join(ROOT, "data", "features.parquet")
    if not os.path.exists(feat_path):
        print("[simulate] features.parquet not found, run pipeline.py first")
        return
    features = pd.read_parquet(feat_path)

    convergence_rows = []
    alpha = initial_alpha  # 初始修正系数(起步偏紧)

    for period in range(n_periods):
        print(f"\n{'='*50}")
        print(f"[simulate] Period {period + 1}/{n_periods}")
        print(f"{'='*50}")

        feat_subset = features.copy()

        # ---- C-1 预测层 ----
        pred_q10, pred_q50, pred_q90, y_test, naive_pred, mape_raw, naive_mape = predict(
            feat_subset, params, period
        )
        print(f"  [predict] raw_mape={mape_raw:.4f}, naive_mape={naive_mape:.4f}")

        # ---- C-2 校准层 ----
        alpha, _ = calibrate(
            feat_subset, params, period, prev_alpha=alpha,
            pred_q50=pred_q50, y_test=y_test
        )

        # ---- 用 alpha 调整预测后重新计算 MAPE ----
        adjusted_pred = pred_q50 * alpha
        adjusted_mape = _safe_mape(y_test, adjusted_pred)

        # ---- 偏紧信号占比 ----
        tight_signal_ratio = float(np.mean(adjusted_pred < y_test))

        print(f"  [calibrate] alpha={alpha:.4f}")
        print(f"  [adjusted] mape={adjusted_mape:.4f} (raw={mape_raw:.4f}, gap={adjusted_mape-mape_raw:+.4f})")
        print(f"  [tight]    ratio={tight_signal_ratio:.4f}")

        # ---- C-3 护栏层抽检 ----
        for txt in ["稳赚不赔的投资机会来了"]:
            passed, hits = guard(txt, params)
            if not passed:
                print(f"  [guard] BLOCKED: '{txt}' -> hits={hits}")

        # ---- 记录收敛指标(真实计算值) ----
        convergence_rows.append({
            "period": period + 1,
            "mape": round(adjusted_mape, 4),
            "raw_mape": round(mape_raw, 4),
            "gap": round(adjusted_mape - mape_raw, 4),
            "naive_mape": round(naive_mape, 4),
            "tight_signal_ratio": round(tight_signal_ratio, 4),
            "alpha": round(alpha, 4),
            "n_windows": len(feat_subset),
        })

    # ---- 输出（由根目录脚本写盘）----
    conv_df = pd.DataFrame(convergence_rows)

    # ---- 验证收敛形态: 打印全曲线 + 逐段判断 ----
    print(f"\n{'='*50}")
    print("[simulate] 收敛验证 (全曲线逐段):")
    print(f"{'='*50}")
    mape_vals = conv_df["mape"].values
    tight_vals = conv_df["tight_signal_ratio"].values
    alpha_vals = conv_df["alpha"].values

    print(f"  MAPE 序列:        {[round(v,4) for v in mape_vals]}")
    print(f"  偏紧占比序列:     {[round(v,4) for v in tight_vals]}")
    print(f"  alpha 序列:       {[round(v,4) for v in alpha_vals]}")
    print(f"  窗口数:           {list(conv_df['n_windows'].values)}")

    # MAPE 逐段
    print("\n  MAPE 逐段:")
    for i in range(len(mape_vals) - 1):
        delta = mape_vals[i + 1] - mape_vals[i]
        flag = "降" if delta < 0 else "升"
        print(f"    P{i+1}->P{i+2}: {mape_vals[i]:.4f} -> {mape_vals[i+1]:.4f} ({flag} {abs(delta):.4f})")

    # 偏紧占比逐段
    print("\n  偏紧占比逐段:")
    for i in range(len(tight_vals) - 1):
        delta = tight_vals[i + 1] - tight_vals[i]
        flag = "降" if delta < 0 else "升"
        print(f"    P{i+1}->P{i+2}: {tight_vals[i]:.4f} -> {tight_vals[i+1]:.4f} ({flag} {abs(delta):.4f})")

    # 关键形态
    mape_drop_p12 = mape_vals[0] - mape_vals[1] if len(mape_vals) >= 2 else 0
    tight_monotone = all(tight_vals[i] >= tight_vals[i + 1] for i in range(len(tight_vals) - 1))
    alpha_monotone = all(alpha_vals[i] <= alpha_vals[i + 1] for i in range(len(alpha_vals) - 1))

    print(f"\n  [形态1] MAPE P1->P2 下降: {mape_drop_p12:.4f} ({'OK' if mape_drop_p12 > 0 else 'CHECK'})")
    print(f"  [形态2] 偏紧占比单调下降: {'OK' if tight_monotone else 'CHECK'}")
    print(f"  [形态3] alpha 单调上升:   {'OK' if alpha_monotone else 'CHECK'}")

    print(f"\n  合成数据回放，非精度承诺。seed={seed}")
    return conv_df


def run_sensitivity():
    """参数敏感性分析: 初始步长 x 衰减率二维网格，真跑4周期仿真"""
    params = load_params()
    sens_cfg = params["sensitivity"]
    step_grid = sens_cfg["initial_step_grid"]
    decay_grid = sens_cfg["decay_grid"]
    initial_alpha = params["model"]["calibrate"].get("alpha_init", 0.85)

    feat_path = os.path.join(ROOT, "data", "features.parquet")
    if not os.path.exists(feat_path):
        print("[sensitivity] features.parquet not found, skipping")
        return
    features = pd.read_parquet(feat_path)

    rows = []
    total = len(step_grid) * len(decay_grid)
    idx = 0
    for step in step_grid:
        for decay in decay_grid:
            idx += 1
            print(f"  [sensitivity] {idx}/{total}: step={step}, decay={decay}")

            alpha = initial_alpha
            final_mape = 0.0
            final_tight = 0.0
            for period in range(4):
                pred_q10, pred_q50, pred_q90, y_test, naive_pred, mape_raw, naive_mape = predict(
                    features, params, period
                )

                cal_cfg = params["model"]["calibrate"].copy()
                cal_cfg["initial_step"] = step
                cal_cfg["decay"] = decay
                local_params = params.copy()
                local_params["model"] = params["model"].copy()
                local_params["model"]["calibrate"] = cal_cfg

                alpha, _ = calibrate(
                    features, local_params, period, prev_alpha=alpha,
                    pred_q50=pred_q50, y_test=y_test
                )

                adjusted_pred = pred_q50 * alpha
                adjusted_mape = _safe_mape(y_test, adjusted_pred)
                tight_signal_ratio = float(np.mean(adjusted_pred < y_test))

                final_mape = adjusted_mape
                final_tight = tight_signal_ratio

            rows.append({
                "initial_step": step,
                "decay": decay,
                "final_mape": round(final_mape, 4),
                "final_tight_ratio": round(final_tight, 4),
            })

    sens_df = pd.DataFrame(rows)
    return sens_df


if __name__ == "__main__":
    conv_df = run_simulation()
    print()
    sens_df = run_sensitivity()
    # 保存结果到 results/
    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    conv_df.to_csv(os.path.join(ROOT, "results", "convergence.csv"), index=False)
    sens_df.to_csv(os.path.join(ROOT, "results", "sensitivity.csv"), index=False)
    print(f"\n[simulate] saved results/convergence.csv ({len(conv_df)} rows)")
    print(f"[simulate] saved results/sensitivity.csv ({len(sens_df)} rows)")
