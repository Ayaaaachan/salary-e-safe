# 合成数据说明 (README_data.md)

> **合成数据回放，非精度承诺。** 本文件描述的所有参数分布与生成逻辑仅用于仿真演示与答辩，绝不代表真实客户数据，未对接任何银行接口。

## 1. 数据规模

| 参数 | 值 | 来源 |
|------|-----|------|
| 用户数 n_users | 200 | params.json → data.n_users |
| 月数 n_months | 12 | params.json → data.n_months |
| 随机种子 random_seed | 20260926 | params.json → random_seed |
| 起始日期 start_date | 2025-01-01 | params.json → data.start_date |

## 2. 用户画像参数分布

### 2.1 月薪 salary
- **分布类型**: 对数正态分布 Lognormal(μ=8.9, σ=0.35)
- **裁剪范围**: [4000, 15000] 元
- **选择理由**: 收入数据在现实中右偏，对数正态比均匀分布更贴合。μ=8.9 对应中位数约 e^8.9 ≈ 7332 元，σ=0.35 控制离散度。
- **生成逻辑**: `rng.lognormal(mean=8.9, sigma=0.35)` 循环采样直至落入 [4000, 15000]。

### 2.2 发薪日 payday
- **取值集合**: {5, 10, 15, 20}（每月 5/10/15/20 日）
- **分布**: 等概率随机选择
- **选择理由**: 企业发薪日集中在月中节点，覆盖月初(5)、上旬末(10)、中旬(15)、下旬初(20)。

### 2.3 房租 rent
- **占月收入比**: 截断正态分布，中心 1/3 (33.33%)，σ=0.04
- **裁剪范围**: [25%, 40%]
- **数据锚点**: 以「74.0% 租房青年房租占收入 1/3」为分布中心
- **房租金额**: salary × rent_ratio

### 2.4 信用还款 credit_repayment
- **笔数**: 1~2 笔，均匀分布
- **占收入比**: [5%, 15%]，均匀分布
- **还款日期**: 发薪日后 7~20 天内，±3 天偏移

## 3. 流水事件生成逻辑

### 3.1 发薪入账
- 每月发薪日当天，金额 = +salary
- 若发薪日超过当月天数，取当月最后一天

### 3.2 刚性支出
- **房租**: 发薪日 ±3 天内扣款，金额 = -rent_amount
- **信用还款**: 发薪日后 7~20 天，±3 天偏移，每笔 = -credit_amount / credit_count
- **余额约束**: 支出前检查余额，不足则跳过该笔（避免负余额）

### 3.3 随机日消费（三种真实效应）

| 效应 | 参数 | 作用 |
|------|------|------|
| 发薪日后 3 天消费峰值 | post_payday_peak_days=3, multiplier=2.2 | 发薪日后 3 天内日消费 ×2.2 |
| 周末消费上升 | weekend_multiplier=1.5 | 周六周日日消费 ×1.5 |
| 月末消费收缩 | month_end_days=5, contraction=0.45 | 月末最后 5 天日消费 ×0.45 |

- **基础日消费**: salary × 0.012 (daily_spending_base_ratio)
- **每日笔数**: 1~3 笔，每笔金额 = |Normal(base, base×0.3)|
- **叠加规则**: 各效应乘数可叠加（如发薪日后周末 = 2.2 × 1.5）

### 3.4 真实感噪声

#### 偶发大额支出
- **概率**: 每月 12% 概率发生
- **金额**: 月薪的 30%~120%
- **类型**: large_expense（医疗/人情）

#### 节日脉冲
| 节日 | 日期 | 乘数 | 窗口 |
|------|------|------|------|
| 春节 | 2月10日 | ×2.5 | ±7天 |
| 国庆 | 10月1日 | ×2.0 | ±7天 |

#### 月份天数差异
- 使用 `month_days(year, month)` 正确计算每月天数（含跨年处理）

## 4. 输出文件

### users.csv
| 列名 | 含义 | 类型 |
|------|------|------|
| user_id | 用户ID | string (U0001~U0200) |
| salary | 月薪 | float |
| payday | 发薪日 | int (5/10/15/20) |
| rent_ratio | 房租占收入比 | float |
| rent_amount | 房租金额 | float |
| credit_count | 信用还款笔数 | int |
| credit_ratio | 信用还款占收入比 | float |
| credit_amount | 信用还款总额 | float |

### transactions.csv
| 列名 | 含义 | 类型 |
|------|------|------|
| user_id | 用户ID | string |
| date | 交易日期 | string (YYYY-MM-DD) |
| amount | 交易金额 | float (正=收入, 负=支出) |
| type | 交易类型 | string (salary/rent/credit_repayment/spending/large_expense) |
| balance_after | 交易后余额 | float |

## 5. 可复现性

```bash
python src/gen_data.py --n 200 --m 12 --seed 20260926
```

固定种子 20260926 保证完全可复现。所有参数来自 `params.json`，无硬编码。

## 6. 验收要点

- 单用户余额曲线：发薪日跳高 → 月中下滑 → 月末偏低
- 发薪日不硬编码传给下游：pipeline.py 中 `detect_payday_for_user()` 从流水自动识别
