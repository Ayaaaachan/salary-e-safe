"""
工银薪e安 - 体验版后端 experience.py
合成数据回放，非精度承诺。本地离线运行，不调用外部API。

引导式问答 -> 预算规划 -> 分账建议
启动: python src/experience.py
"""
import os
import json
import numpy as np
from flask import Flask, request, jsonify

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__)

# 加载参数
with open(os.path.join(ROOT, "params.json"), "r", encoding="utf-8") as f:
    PARAMS = json.load(f)


# ============ 引导式问答流程 ============
# 5 个问题，按顺序收集用户信息
QUESTIONS = [
    {
        "id": "salary",
        "question": "你每月税后到手工资大约多少？",
        "hint": "填整数即可，比如 8000",
        "type": "number",
        "unit": "元",
        "validate": lambda v: 1000 <= v <= 100000,
        "error": "请输入 1000-100000 之间的数字",
    },
    {
        "id": "payday",
        "question": "你每月几号发工资？",
        "hint": "比如 10 号、15 号、20 号",
        "type": "choice",
        "options": [5, 10, 15, 20, "其他"],
        "validate": lambda v: v in [5, 10, 15, 20] or isinstance(v, int),
        "error": "请选择或输入发薪日",
    },
    {
        "id": "rent",
        "question": "每月固定房租/房贷多少？",
        "hint": "没有填 0",
        "type": "number",
        "unit": "元",
        "validate": lambda v: 0 <= v <= 50000,
        "error": "请输入 0-50000 之间的数字",
    },
    {
        "id": "credit",
        "question": "每月信用还款（花呗/信用卡/借呗）多少？",
        "hint": "没有填 0",
        "type": "number",
        "unit": "元",
        "validate": lambda v: 0 <= v <= 50000,
        "error": "请输入 0-50000 之间的数字",
    },
    {
        "id": "daily_spend",
        "question": "平均每天日常消费（吃饭/交通/购物）大约多少？",
        "hint": "比如 80、120、150",
        "type": "number",
        "unit": "元/天",
        "validate": lambda v: 10 <= v <= 2000,
        "error": "请输入 10-2000 之间的数字",
    },
]

# 会话存储（本地演示，单用户）
SESSION = {}


@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "msg": "工银薪e安 体验版后端运行中"})


@app.route("/api/guide/start", methods=["POST"])
def guide_start():
    """开始引导问答，返回第一个问题"""
    SESSION.clear()
    SESSION["step"] = 0
    q = QUESTIONS[0]
    return jsonify({
        "step": 0,
        "total": len(QUESTIONS),
        "question": q["question"],
        "hint": q["hint"],
        "type": q["type"],
        "unit": q.get("unit", ""),
        "options": q.get("options", []),
        "id": q["id"],
    })


@app.route("/api/guide/answer", methods=["POST"])
def guide_answer():
    """提交答案，返回下一个问题或最终结果"""
    data = request.json or {}
    step = SESSION.get("step", 0)

    if step >= len(QUESTIONS):
        return jsonify({"error": "问答已完成，请调用 /api/plan 获取规划"}), 400

    q = QUESTIONS[step]
    value = data.get("value")

    # 类型转换
    if q["type"] == "number":
        try:
            value = int(value)
        except (ValueError, TypeError):
            return jsonify({"error": q["error"]}), 400
    elif q["type"] == "choice":
        try:
            value = int(value) if str(value).isdigit() else value
        except Exception:
            pass

    # 验证
    if not q["validate"](value):
        return jsonify({"error": q["error"]}), 400

    # 存储
    SESSION[q["id"]] = value
    SESSION["step"] = step + 1

    # 返回下一个问题或完成
    if SESSION["step"] < len(QUESTIONS):
        next_q = QUESTIONS[SESSION["step"]]
        return jsonify({
            "step": SESSION["step"],
            "total": len(QUESTIONS),
            "question": next_q["question"],
            "hint": next_q["hint"],
            "type": next_q["type"],
            "unit": next_q.get("unit", ""),
            "options": next_q.get("options", []),
            "id": next_q["id"],
            "progress": f"{SESSION['step']}/{len(QUESTIONS)}",
        })
    else:
        # 问答完成，生成规划
        plan = generate_plan(SESSION)
        return jsonify({
            "done": True,
            "plan": plan,
        })


# ============ 预算规划引擎 ============
def generate_plan(session):
    """根据用户输入生成预算规划和分账建议"""
    salary = session.get("salary", 8000)
    payday = session.get("payday", 10)
    rent = session.get("rent", 0)
    credit = session.get("credit", 0)
    daily_spend = session.get("daily_spend", 100)

    # 固定支出
    fixed_total = rent + credit
    fixed_ratio = fixed_total / salary if salary > 0 else 0

    # 月度日常消费预估
    monthly_daily = daily_spend * 30

    # 总支出
    total_spend = fixed_total + monthly_daily
    surplus = salary - total_spend
    surplus_ratio = surplus / salary if salary > 0 else 0

    # ---- 分账建议（三池模型）----
    # 1. 固定池: 房租+还款（发薪日自动划转）
    fixed_pool = fixed_total

    # 2. 灵活池: 日常消费（按周划转，控制节奏）
    flexible_pool = monthly_daily

    # 3. 储蓄池: 结余（发薪日自动锁定）
    savings_pool = max(surplus, 0)

    # ---- 预警判定 ----
    warnings = []
    if fixed_ratio > 0.5:
        warnings.append("固定支出占比超过50%，现金流压力较大")
    elif fixed_ratio > 0.4:
        warnings.append("固定支出占比偏高(40-50%)，注意控制")
    if surplus_ratio < 0.1:
        warnings.append("月度结余不足10%，建议减少非必要消费")
    if surplus < 0:
        warnings.append("支出超过收入！建议立即调整消费结构")

    # ---- 发薪日周期预测 ----
    # 模拟一个周期的余额走势(基于真实日历，不截断负余额以显示透支)
    import datetime as _dt
    days_in_period = 30
    # 用当前年月的发薪日作为起点(真实星期)
    _today = _dt.date.today()
    _paydate = _dt.date(_today.year, _today.month, min(payday, 28))
    # 信用还款日: 发薪日后 7~20 天内随机(与 README_data.md 一致)
    import random as _rnd
    _rnd.seed(hash((salary, payday, rent, credit, daily_spend)) % 2**32)
    credit_day = _rnd.randint(7, 20)

    balance_curve = []
    balance = float(salary)  # 发薪日余额
    for day in range(days_in_period):
        cur_date = _paydate + _dt.timedelta(days=day)
        if day == 0:
            balance = float(salary)
        # 房租: 发薪日后 1~3 天内扣除
        if day == 1:
            balance -= rent
        # 信用还款: 发薪日后 7~20 天(随机日)
        if day == credit_day:
            balance -= credit
        # 日常消费
        balance -= daily_spend
        # 真实周末加成 (周六=5, 周日=6)
        if cur_date.weekday() in [5, 6]:
            balance -= daily_spend * 0.5
        # 不截断负余额: 保留透支信号(这是核心风控信号)
        balance_curve.append(round(balance, 2))

    # 预计月末余额
    end_balance = balance_curve[-1]
    end_balance_ratio = end_balance / salary if salary > 0 else 0

    # ---- 个性化建议 ----
    tips = []
    if fixed_ratio > 0.4:
        tips.append("固定支出偏高，建议考虑合租或协商还款期")
    if daily_spend > salary * 0.015:
        tips.append("日均消费偏高，建议设置每日消费上限")
    if surplus_ratio > 0.2:
        tips.append("结余充足，建议配置自动转存（余额宝/定期）")
    if end_balance_ratio < 0.1:
        tips.append("月末余额偏低，建议预留至少月薪10%作为缓冲")

    # ---- 健康评分 ----
    score = 100
    if fixed_ratio > 0.5:
        score -= 30
    elif fixed_ratio > 0.4:
        score -= 15
    if surplus_ratio < 0.1:
        score -= 25
    if surplus < 0:
        score -= 40
    if daily_spend > salary * 0.015:
        score -= 15
    score = max(score, 0)

    return {
        "summary": {
            "salary": salary,
            "payday": payday,
            "fixed_total": fixed_total,
            "fixed_ratio": round(fixed_ratio, 4),
            "monthly_daily_spend": monthly_daily,
            "total_spend": total_spend,
            "surplus": round(surplus, 2),
            "surplus_ratio": round(surplus_ratio, 4),
            "end_balance": round(end_balance, 2),
            "end_balance_ratio": round(end_balance_ratio, 4),
            "health_score": score,
        },
        "split": {
            "fixed_pool": round(fixed_pool, 2),
            "flexible_pool": round(flexible_pool, 2),
            "savings_pool": round(savings_pool, 2),
            "fixed_pool_pct": round(fixed_pool / salary * 100, 1) if salary > 0 else 0,
            "flexible_pool_pct": round(flexible_pool / salary * 100, 1) if salary > 0 else 0,
            "savings_pool_pct": round(savings_pool / salary * 100, 1) if salary > 0 else 0,
        },
        "balance_curve": balance_curve,
        "warnings": warnings,
        "tips": tips,
        "note": "合成数据回放，非精度承诺。建议仅供参考。",
    }


@app.route("/api/plan", methods=["POST"])
def plan():
    """直接提交全部信息获取规划（跳过引导问答）"""
    data = request.json or {}
    session = {
        "salary": data.get("salary", 8000),
        "payday": data.get("payday", 10),
        "rent": data.get("rent", 0),
        "credit": data.get("credit", 0),
        "daily_spend": data.get("daily_spend", 100),
    }
    return jsonify({"plan": generate_plan(session)})


@app.route("/api/guard", methods=["POST"])
def guard_check():
    """护栏层演示：检测文案是否合规"""
    from layers import guard, load_params
    data = request.json or {}
    text = data.get("text", "")
    passed, hits = guard(text, PARAMS)
    return jsonify({"passed": passed, "hits": hits, "text": text})


if __name__ == "__main__":
    print("[experience] 工银薪e安 体验版后端")
    print("[experience] 合成数据回放，非精度承诺")
    print("[experience] 启动: http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
