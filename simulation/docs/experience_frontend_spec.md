# 体验版前端修改意见

> 后端已就绪: `src/experience.py` (Flask, http://127.0.0.1:5000)
> 所有接口本地调用，不涉及外部服务。合成数据回放，非精度承诺。

---

## 新增页面: 体验模式

### 入口
- 首页加一个「立即体验」按钮，点击进入引导问答
- 不需要登录，纯本地交互

### 1. 引导问答组件 (5步)

后端接口:
- `POST /api/guide/start` -> 返回第一个问题
- `POST /api/guide/answer` (body: {value: 用户输入}) -> 返回下一个问题或最终规划

UI 建议:
- 单页逐步展示，顶部进度条 (1/5, 2/5...)
- 数字输入用带单位的输入框 (后缀"元"或"元/天")
- 选择题用卡片式按钮 (5号/10号/15号/20号/其他)
- 每步有"上一步"按钮
- 最后一步提交后，平滑过渡到结果页

### 2. 结果展示页

后端返回的 plan 对象包含:

```
{
  "summary": { salary, payday, fixed_total, fixed_ratio, surplus, health_score, ... },
  "split": { fixed_pool, flexible_pool, savings_pool, 各池占比 },
  "balance_curve": [30天的余额数组],
  "warnings": ["预警文案"],
  "tips": ["建议文案"],
  "note": "合成数据回放，非精度承诺"
}
```

#### 2.1 健康评分卡片
- 圆形仪表盘 (0-100分)
- 颜色: 80+绿色 / 60-80黄色 / <60红色
- 下方一行: "现金流健康度"

#### 2.2 三池分账可视化
- 三个卡片或环形图: 固定池 / 灵活池 / 储蓄池
- 每个卡片显示金额 + 占比
- 固定池 = 房租+还款（发薪日自动划转）
- 灵活池 = 日常消费（按周释放）
- 储蓄池 = 结余（锁定不动）

#### 2.3 余额走势曲线
- ECharts 折线图，X轴=天数(0-29)，Y轴=余额(元)
- 标注发薪日(第0天)和固定支出扣除点(第1/3天)
- 月末余额用虚线标注
- 用 balance_curve 数组直接画

#### 2.4 预警和建议
- warnings 红色卡片列表
- tips 蓝色卡片列表
- 底部固定标注: "合成数据回放，非精度承诺"

### 3. 护栏演示组件

后端接口: `POST /api/guard` (body: {text: "文案"}) -> {passed, hits}

UI:
- 输入框 + "检测"按钮
- 通过: 绿色提示 "文案合规"
- 拦截: 红色提示 "命中违规词: 稳赚, 保本..."
- 可加几个预设文案按钮快速测试

---

## 技术要求

1. **启动后端**: `cd E:\工银薪e安_仿真项目 && .venv\Scripts\pip install flask && .venv\Scripts\python src\experience.py`
2. 前端请求 baseURL: `http://127.0.0.1:5000`
3. 所有接口都是 POST + JSON body，返回 JSON
4. 不需要鉴权，纯本地演示
5. 页面底部统一标注"合成数据回放，非精度承诺"

---

## 接口速查

| 接口 | 方法 | 入参 | 返回 |
|------|------|------|------|
| /api/health | GET | - | {status: "ok"} |
| /api/guide/start | POST | - | 第一个问题 |
| /api/guide/answer | POST | {value: 用户输入} | 下一题或最终plan |
| /api/plan | POST | {salary, payday, rent, credit, daily_spend} | plan对象 |
| /api/guard | POST | {text: "文案"} | {passed, hits} |
