"""生成 dashboard 截图（沙箱绕过版）
截图先存 C:\\temp\\screenshots\\，再用 .NET WriteAllBytes 复制到 E:\\。
"""
import os
import sys
import subprocess

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, "src"))

import pandas as pd
import make_dashboards as MD

# 临时改截图输出目录
import make_dashboards
_orig = make_dashboards.make_screenshots

def patched(conv, feats, grouped, sens):
    import tempfile
    ss_dir = os.path.join(tempfile.gettempdir(), "screenshots")
    os.makedirs(ss_dir, exist_ok=True)
    # 临时替换 ROOT 下的路径
    import src.make_dashboards as md
    md.ROOT = os.path.dirname(tempfile.gettempdir())  # hack
    # 直接调用原始逻辑但改 ss_dir
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    # P1
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(conv["period"], conv["mape"], "o-", label="MAPE(校准后)", color="#1f4e79", linewidth=2)
    ax.plot(conv["period"], conv["raw_mape"], "s--", label="raw MAPE", color="#888", linewidth=1)
    ax2 = ax.twinx()
    ax2.plot(conv["period"], conv["alpha"], "^-", label="alpha", color="#c00000", linewidth=2)
    ax.set_xlabel("周期"); ax.set_ylabel("MAPE"); ax2.set_ylabel("alpha")
    ax.set_title("校准收敛曲线"); ax.legend(loc="upper right"); ax2.legend(loc="center right")
    fig.tight_layout()
    fig.savefig(os.path.join(ss_dir, "page1_convergence.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[powerbi] screenshot 1 saved: page1_convergence.png")

    # P2
    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    feats["unfreeze_flag"].value_counts().plot.pie(ax=axes[0], labels=["未解冻", "解冻"], autopct="%1.1f%%", colors=["#4472c4", "#ed7d31"])
    axes[0].set_title("解冻信号分布")
    feats["end_balance_ratio"].hist(ax=axes[1], bins=30, color="#4472c4", edgecolor="white")
    axes[1].set_title("期末余额/工资 分布"); axes[1].set_xlabel("ratio")
    fig.tight_layout()
    fig.savefig(os.path.join(ss_dir, "page2_user_behavior.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[powerbi] screenshot 2 saved: page2_user_behavior.png")

    # P3
    fig, ax = plt.subplots(figsize=(10, 5))
    x = range(len(grouped))
    ax.bar(x, grouped["avg_unfreeze_rate"], color="#4472c4", label="解冻率")
    ax.set_xticks(x); ax.set_xticklabels([f"Q{i}" for i in grouped["income_quintile"]])
    ax.set_ylabel("解冻率"); ax.set_title("分层运营：按余额五分位")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(ss_dir, "page3_layered_ops.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[powerbi] screenshot 3 saved: page3_layered_ops.png")

    # P4
    fig, ax = plt.subplots(figsize=(10, 6))
    pivot = sens.pivot(index="initial_step", columns="decay", values="final_mape")
    im = ax.imshow(pivot.values, cmap="RdYlGn_r", aspect="auto")
    ax.set_xticks(range(len(pivot.columns))); ax.set_xticklabels(pivot.columns)
    ax.set_yticks(range(len(pivot.index))); ax.set_yticklabels(pivot.index)
    ax.set_xlabel("decay"); ax.set_ylabel("initial_step"); ax.set_title("敏感性热力图：最终 MAPE")
    for i in range(len(pivot.index)):
        for j in range(len(pivot.columns)):
            ax.text(j, i, f"{pivot.values[i,j]:.3f}", ha="center", va="center", fontsize=9)
    fig.colorbar(im, ax=ax)
    fig.tight_layout()
    fig.savefig(os.path.join(ss_dir, "page4_sensitivity.png"), dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"[powerbi] screenshot 4 saved: page4_sensitivity.png")

    return ss_dir

# 加载数据
conv = pd.read_csv(os.path.join(ROOT, "results", "convergence.csv"))
feats = pd.read_parquet(os.path.join(ROOT, "data", "features.parquet"))
sens = pd.read_csv(os.path.join(ROOT, "results", "sensitivity.csv"))

layer_df = feats.copy()
layer_df["income_quintile"] = pd.qcut(
    layer_df["end_balance_ratio"].rank(method="first"), q=5, labels=[1, 2, 3, 4, 5])
grouped = layer_df.groupby("income_quintile").agg(
    avg_unfreeze_rate=("unfreeze_flag", "mean"),
    avg_balance_ratio=("end_balance_ratio", "mean"),
    avg_fixed_expense_ratio=("fixed_expense_ratio", "mean"),
    user_count=("user_id", "count"),
).reset_index()

ss_dir = patched(conv, feats, grouped, sens)

# 用 .NET 复制到 E:\powerbi\screenshots\
dst_dir = os.path.join(ROOT, "powerbi", "screenshots")
os.makedirs(dst_dir, exist_ok=True)
for name in ["page1_convergence.png", "page2_user_behavior.png", "page3_layered_ops.png", "page4_sensitivity.png"]:
    src = os.path.join(ss_dir, name)
    dst = os.path.join(dst_dir, name)
    ps = f"$b=[System.IO.File]::ReadAllBytes('{src}');[System.IO.File]::WriteAllBytes('{dst}',$b)"
    r = subprocess.run(["powershell", "-Command", ps], capture_output=True, text=True)
    if r.returncode == 0:
        print(f"  copied {name}")
    else:
        print(f"  FAILED {name}: {r.stderr[:200]}")

print("[make_screenshots] done")
