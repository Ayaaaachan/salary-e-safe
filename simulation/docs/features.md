# 特征表字段说明（合成数据回放，非精度承诺）

| 英文列名 | 中文含义 | 取值范围 | 来源 | 是否脱敏 |
|----------|----------|----------|------|----------|
| user_id | 用户哈希ID | 哈希字符串 | 用户主键 | 是 |
| window_id | 窗口哈希ID | 哈希字符串 | 窗口主键 | 是 |
| period_days | 周期天数 | 正整数 | 窗口长度 | 否 |
| days_since_payday | 距发薪日天数 | 整数 | 窗口起点距发薪日 | 否 |
| fixed_expense_total | 固定支出总额 | ≥0 元 | 房租+信用还款 | 否 |
| fixed_expense_ratio | 固定支出占收入比 | [0,1] | 固定支出/工资 | 否 |
| repayment_days | 还款日集合 | 逗号分隔日号 | 房租/还款日 | 否 |
| weekend_spend_ratio | 周末支出占比 | [0,1] | 周末支出/总支出 | 否 |
| tx_count | 交易笔数 | ≥0 整数 | 窗口内有效交易数 | 否 |
| unfreeze_flag | 是否解冻(钱不够花) | 0/1 | 余额跌破0(透支) | 否 |
| unfreeze_day | 解冻日距发薪天数 | 整数 | 首次跌破0日距发薪日天数 | 否 |
| prev_unfreeze_flag | 上周期是否解冻 | 0/1 | 上周期解冻标记 | 否 |
| prev_unfreeze_day | 上周期解冻日距发薪天数 | 整数 | 上周期解冻偏移 | 否 |
| salary_bin | 工资分箱 | 1-4 | 工资四分位分箱 | 是 |
| end_balance_ratio | 期末余额占收入比 | 实数 | 期末余额/工资 | 是 |
| prev_end_balance_ratio | 上周期末余额占收入比 | 实数 | 上周期末余额/工资 | 是 |
| unfreeze_amount_ratio | 解冻金额占收入比 | ≥0 | 解冻金额/工资 | 是 |
| prev_unfreeze_amount_ratio | 上周期解冻金额占收入比 | ≥0 | 上周期解冻金额/工资 | 是 |
| balance_diff | 余额比率时序差分 | 实数 | 本期-上期余额比率 | 是 |
| anonymized | 脱敏标记 | True | 是否已脱敏 | 否 |