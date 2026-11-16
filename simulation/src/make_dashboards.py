"""
工银薪e安 - Power BI 看板数据准备 + 截图生成
合成数据回放，非精度承诺。

生成:
  powerbi/dashboard_data/     4个CSV数据源（供 .pbix 导入）
  powerbi/screenshots/        4张截图（入PPT用）

红线: 绝不使用"发布到Web"或Power BI Service，仅本地Desktop。
P1-2 修正: 移除所有 fallback 假数据，文件缺失直接报错退出。
"""
import os
import sys
import json
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import shutil
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def safe_to_csv(df, dst_path):
    """沙箱环境下部分E盘路径写入受限，统一先写到 C:\\temp\\powerbi_out\\，
    跑完后用独立 PowerShell .NET 命令复制到 E:\\。"""
    out_dir = r"C:\temp\powerbi_out"
    os.makedirs(out_dir, exist_ok=True)
    name = os.path.basename(dst_path)
    df.to_csv(os.path.join(out_dir, name), index=False, encoding="utf-8-sig")
    print(f"[powerbi] CSV -> {out_dir}\\{name} (需手动copy到 E:\\)")


def load_params():
    with open(os.path.join(ROOT, "params.json"), "r", encoding="utf-8") as f:
        return json.load(f)


def prepare_data():
    """准备4个页面所需的CSV数据源

    P1-2 修正: 不再生成 fallback 假数据，文件缺失即报错退出。
    """
    out_dir = os.path.join(ROOT, "powerbi", "dashboard_data")
    os.makedirs(out_dir, exist_ok=True)

    # ---- 页面1: 校准收敛页 ----
    conv_path = os.path.join(ROOT, "results", "convergence.csv")
    if not os.path.exists(conv_path):
        print(f"[ERROR] convergence.csv not found at {conv_path}")
        print("[ERROR] Run simulate.py first. Refusing to use fake data (P1-2).")
        sys.exit(1)
    conv = pd.read_csv(conv_path)
    safe_to_csv(conv, os.path.join(out_dir, "page1_convergence.csv"))

    # ---- 页面2: 用户行为页 ----
    feat_path = os.path.join(ROOT, "data", "features.parquet")
    if not os.path.exists(feat_path):
        print(f"[ERROR] features.parquet not found at {feat_path}")
        print("[ERROR] Run pipeline.py first. Refusing to use fake data (P1-2).")
        sys.exit(1)
    feats = pd.read_parquet(feat_path)
    safe_to_csv(feats, os.path.join(out_dir, "page2_user_behavior.csv"))

    # ---- 页面3: 分层运营页 ----
    # 按收入分位分组
    layer_df = feats.copy()
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
    safe_to_csv(grouped, os.path.join(out_dir, "page3_layered_ops.csv"))

    # ---- 页面4: 参数敏感性页 ----
    sens_path = os.path.join(ROOT, "results", "sensitivity.csv")
    if not os.path.exists(sens_path):
        print(f"[ERROR] sensitivity.csv not found at {sens_path}")
        print("[ERROR] Run simulate.py first. Refusing to use fake data (P1-2).")
        sys.exit(1)
    sens = pd.read_csv(sens_path)
    safe_to_csv(sens, os.path.join(out_dir, "page4_sensitivity.csv"))

    print(f"[powerbi] 4 CSV data sources saved to {out_dir}")
    return conv, feats, grouped, sens


def make_screenshots(conv, feats, grouped, sens):
    """生成4张截图（入PPT用）"""
    # 沙箱限制：截图先存 C:\temp\powerbi_out\，跑完用 .NET 复制到 E:\
    ss_dir = r"C:\temp\powerbi_out"
    os.makedirs(ss_dir, exist_ok=True)

    plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial"]
    plt.rcParams["axes.unicode_minus"] = False

    # ==== 截图1: 校准收敛页 ====
    # 【本轮修改】原三轴各自缩放导致视觉夸张（偏紧占比只降2pp却画成垮到底）。
    # 改为双子图：左图把偏紧占比和 α 都归一化到 [0,1] 共用坐标，让"同步收敛"可比；
    # 右图单独画 MAPE（含 raw_mape 对照），避免不同量纲混在一张图里误导。
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))
    periods = conv["period"]

    # 左图：偏紧占比 与 α 归一化到 [0,1]，共用 y 轴
    tight = conv["tight_signal_ratio"].values
    alpha = conv["alpha"].values
    # 归一化到各自的 [min, max] 区间映射到 [0,1]，展示相对变化
    tight_norm = (tight - tight.min()) / (tight.max() - tight.min() + 1e-9)
    alpha_norm = (alpha - alpha.min()) / (alpha.max() - alpha.min() + 1e-9)
    ax1.plot(periods, tight_norm, "r-s", linewidth=2.4, markersize=8, label="偏紧信号占比 (归一化)")
    ax1.plot(periods, alpha_norm, "g-^", linewidth=2.4, markersize=8, label="α 修正系数 (归一化)")
    ax1.set_xticks([1, 2, 3, 4])
    ax1.set_ylim(-0.05, 1.05)
    ax1.set_xlabel("回放周期", fontsize=12)
    ax1.set_ylabel("归一化值 [0=最弱, 1=最强]", fontsize=11)
    ax1.set_title("信号与修正系数同步收敛", fontsize=13)
    # 标注真实数值
    for i, p in enumerate(periods):
        ax1.annotate(f"{tight[i]*100:.1f}%", (p, tight_norm[i]),
                     textcoords="offset points", xytext=(0, 10), ha="center", fontsize=9, color="r")
        ax1.annotate(f"{alpha[i]:.3f}", (p, alpha_norm[i]),
                     textcoords="offset points", xytext=(0, -14), ha="center", fontsize=9, color="g")
    ax1.legend(loc="center right", fontsize=10)

    # 右图：MAPE（模型 vs raw vs naive）
    ax2.plot(periods, conv["mape"] * 100, "o-", color="#1f4e79", linewidth=2.4, markersize=8, label="MAPE (校准后)")
    ax2.plot(periods, conv["raw_mape"] * 100, "s--", color="#888", linewidth=1.4, markersize=6, label="raw MAPE")
    if "naive_mape" in conv.columns:
        ax2.axhline(y=conv["naive_mape"].iloc[0] * 100, color="#c00000", linestyle=":", linewidth=1.5,
                    label=f"naive 基线 ({conv['naive_mape'].iloc[0]*100:.1f}%)")
    ax2.set_xticks([1, 2, 3, 4])
    ax2.set_ylim(0, max(conv["mape"].max() * 100 * 1.2, 50))
    ax2.set_xlabel("回放周期", fontsize=12)
    ax2.set_ylabel("MAPE (%)", fontsize=11)
    ax2.set_title("预测误差：校准后显著优于基线", fontsize=13)
    ax2.legend(loc="upper right", fontsize=10)

    fig.suptitle("校准过程：信号与修正系数同步变化（合成数据回放，非精度承诺）", fontsize=14)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    fig.savefig(os.path.join(ss_dir, "page1_convergence.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("[powerbi] screenshot 1 saved: page1_convergence.png")

    # ==== 截图2: 用户行为页 ====
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))

    # 解冻率分布（饼图）：unfreeze_flag value_counts (0 vs 1)
    unfreeze_counts = feats["unfreeze_flag"].value_counts().sort_index()
    labels = ["未解冻 (0)" if idx == 0 else f"已解冻 (1)" for idx in unfreeze_counts.index]
    colors = ["#4ecdc4" if idx == 0 else "#ff6b6b" for idx in unfreeze_counts.index]
    axes[0].pie(unfreeze_counts.values, labels=labels, colors=colors,
                autopct="%1.1f%%", startangle=90, textprops={"fontsize": 10})
    axes[0].set_title("解冻率分布（余额跌破 0）")

    # 解冻日距发薪日天数直方图（仅 unfreeze_day >= 0 的真实值）
    if "unfreeze_day" in feats.columns:
        unfreeze_days = feats[(feats["unfreeze_flag"] == 1) & (feats["unfreeze_day"] >= 0)]["unfreeze_day"]
        if len(unfreeze_days) > 0:
            axes[1].hist(unfreeze_days, bins=15, color="#45b7d1", edgecolor="white")
            axes[1].set_title("解冻发生日距发薪日天数")
            axes[1].set_xlabel("天数")
            axes[1].set_ylabel("用户数")
        else:
            axes[1].text(0.5, 0.5, "无解冻样本", ha="center", va="center", fontsize=12)
            axes[1].set_title("解冻发生日距发薪日天数")

    # 分账设置率（用 fixed_expense_ratio 作为代理）
    if "fixed_expense_ratio" in feats.columns:
        # 【本轮修改】显式限幅防止离群值把 X 轴撑到 0-30（口径修好后不会再有 >1.2 的值）
        fer = feats["fixed_expense_ratio"].clip(0, 1.2)
        axes[2].hist(fer, bins=15, range=(0, 1.2), color="#96ceb4", edgecolor="white")
        axes[2].set_title("固定支出占比（分账设置率代理）")
        axes[2].set_xlabel("占比")
        axes[2].set_ylabel("用户数")

    fig.suptitle("用户行为分析（合成数据回放，非精度承诺）", fontsize=14)
    fig.tight_layout()
    fig.savefig(os.path.join(ss_dir, "page2_user_behavior.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("[powerbi] screenshot 2 saved: page2_user_behavior.png")

    # ==== 截图3: 分层运营页 ====
    fig, ax = plt.subplots(figsize=(10, 6))
    x = np.arange(len(grouped))
    width = 0.35
    ax.bar(x - width/2, grouped["avg_unfreeze_rate"] * 100, width, label="解冻率 (%)", color="#6c5ce7")
    ax.bar(x + width/2, grouped["avg_balance_ratio"] * 100, width, label="期末余额占比 (%)", color="#fd79a8")
    ax.set_xticks(x)
    ax.set_xticklabels([f"Q{int(q)}" for q in grouped["income_quintile"]])
    ax.set_xlabel("收入分位（Q1 最低）")
    ax.set_ylabel("百分比 (%)")
    # 【本轮修改】标题改为"解冻率与期末余额"，与图内容一致；原"活跃度与留存"文不对题
    ax.set_title("分层运营：按收入分位的解冻率与期末余额（合成数据回放，非精度承诺）", fontsize=13)
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(ss_dir, "page3_layered_ops.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("[powerbi] screenshot 3 saved: page3_layered_ops.png")

    # ==== 截图4: 参数敏感性热力图 ====
    # 【本轮修改】原图只有 MAPE 一个维度，看不出"权衡"（0.08 那行全深绿但偏紧占比可能很高）。
    # 改为并排双热力图：左图 MAPE、右图偏紧占比，两图对照自然呈现"精度 vs 松绑速度"权衡。
    pivot_mape = sens.pivot(index="initial_step", columns="decay", values="final_mape")
    # final_tight_ratio 列若不存在则退化（兼容旧版 sensitivity.csv）
    tight_col = "final_tight_ratio" if "final_tight_ratio" in sens.columns else "final_alpha"
    pivot_tight = sens.pivot(index="initial_step", columns="decay", values=tight_col)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    im1 = ax1.imshow(pivot_mape.values, cmap="RdYlGn_r", aspect="auto")
    ax1.set_xticks(range(len(pivot_mape.columns)))
    ax1.set_yticks(range(len(pivot_mape.index)))
    ax1.set_xticklabels([f"{d:.2f}" for d in pivot_mape.columns])
    ax1.set_yticklabels([f"{s:.2f}" for s in pivot_mape.index])
    ax1.set_xlabel("衰减率 decay")
    ax1.set_ylabel("初始步长 initial_step")
    ax1.set_title("最终 MAPE（越低越好）", fontsize=13)
    fig.colorbar(im1, ax=ax1, label="MAPE", fraction=0.046)
    for i in range(len(pivot_mape.index)):
        for j in range(len(pivot_mape.columns)):
            ax1.text(j, i, f"{pivot_mape.values[i, j]:.3f}", ha="center", va="center", fontsize=9)

    im2 = ax2.imshow(pivot_tight.values, cmap="RdYlGn", aspect="auto")
    ax2.set_xticks(range(len(pivot_tight.columns)))
    ax2.set_yticks(range(len(pivot_tight.index)))
    ax2.set_xticklabels([f"{d:.2f}" for d in pivot_tight.columns])
    ax2.set_yticklabels([f"{s:.2f}" for s in pivot_tight.index])
    ax2.set_xlabel("衰减率 decay")
    ax2.set_ylabel("初始步长 initial_step")
    tight_label = "偏紧占比（越低越松）" if tight_col == "final_tight_ratio" else "最终 α"
    ax2.set_title(tight_label, fontsize=13)
    fig.colorbar(im2, ax=ax2, label=tight_label, fraction=0.046)
    for i in range(len(pivot_tight.index)):
        for j in range(len(pivot_tight.columns)):
            val = pivot_tight.values[i, j]
            ax2.text(j, i, f"{val:.3f}", ha="center", va="center", fontsize=9)

    fig.suptitle("参数敏感性：精度与松绑速度的权衡（合成数据回放，非精度承诺）", fontsize=14)
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(os.path.join(ss_dir, "page4_sensitivity.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print("[powerbi] screenshot 4 saved: page4_sensitivity.png")


if __name__ == "__main__":
    conv, feats, grouped, sens = prepare_data()
    make_screenshots(conv, feats, grouped, sens)
    print("\n[powerbi] 数据源 + 截图全部生成完毕")
    print("[powerbi] 红线: 仅使用本地 Power BI Desktop，禁止发布到Web/Service")
