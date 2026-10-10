# workbuddy.md · WorkBuddy 开发对话上下文

本文件记录「麦麦抽奖精算师」项目使用腾讯 WorkBuddy 开发的完整过程，用于麦当劳程序员创意开发大赛 WorkBuddy 专项奖励核验。

## 开发环境

- **开发工具**：腾讯 WorkBuddy（AI 智能体工作台）
- **会话日期**：2026-10-10
- **MCP 接入方式**：WorkBuddy 自定义连接器（配置 MCP）→ `mcd-mcp`（Streamable HTTP，`https://mcp.mcd.cn`）

## 开发过程实录

### 1. 需求分析与情报收集（WorkBuddy WebSearch/WebFetch）

通过 WorkBuddy 完成赛事调研：
- 抓取 M-China 官方仓库 `mcd-developer-innovation-challenge` 的 README、活动规则（activityGuidelines.md）、参赛声明（CONTEST_DECLARATION.md）及实时排行榜 RANKING.md
- 抓取 `mcd-mcp-server` 集成指南，梳理 33 个公开 Tool 的能力边界
- 分析排行榜竞争格局后选定差异化方向：**积分抽奖期望值精算**（当时仅 1 个 1-Star 竞品）

### 2. MCP 接入与能力实测（WorkBuddy 内直接调用）

在 WorkBuddy 中配置 mcd-mcp 连接器并完成全链路验证：
- `initialize` 握手 → `tools/list`（实测返回 35 个工具，含未写入文档的 `query-promotions`、`query-survey-coupon`）
- 实测调用 `now-time-info`、`query-my-account`、`available-coupons` 全部成功

### 3. 真实数据驱动的方案设计（关键转折）

WorkBuddy 调用 MCP 实测发现三个决定项目形态的事实：
1. 「周六麦金喜抽奖」27 档奖品**无「谢谢参与」档**，最低档为 8 积分（变相回血）
2. 官方**未公示中奖概率** → 放弃编造 EV 点估计，改四层价值分层 + 区间判断
3. 商城到店券全部「已下架」→ 汇率锚点失效，进一步验证"硬造汇率=错误"，转向用户画像决策模型

据此设计出核心方法论：**四层价值分层**（无条件回血积分奖 / 有条件变现立减券 / 增值型折扣券 / 大奖权益）+ **用户画像三答案**（不消费→不抽 / 要消费→可抽 / 过期分→必抽）。

### 4. 代码生成与调试（WorkBuddy 生成 + 实测修复）

- WorkBuddy 生成 `skill/actuary.py`（零第三方依赖，纯标准库）与 `skill/SKILL.md`（WorkBuddy 技能规范）
- 实测发现并修复 bug：`json.dumps(d.keys())` 需转 list
- 实测发现 MCP 返回格式差异（部分工具 JSON 内嵌说明文字），编写 `extract_json` 稳健提取函数
- 最终 `python skill/actuary.py` 在 WorkBuddy 终端以真实账户数据跑出完整精算报告

### 5. 文档体系与发布（WorkBuddy 生成）

- README.md（含真实运行输出示例）、MCP_INTEGRATION.md（调用链与业务价值）、mcp-config.example.json（仅环境变量占位符）均由 WorkBuddy 生成
- CONTEST_DECLARATION.md 使用官方模板原文，未做任何修改
- 通过 WorkBuddy 完成 git 初始化、首次提交与推送至本仓库

## 真实运行输出（2026-10-10 实测）

```
📊 麦麦抽奖精算报告
──────────────────────────────────────────────
💰 积分资产：可用 59.80 分
    → 可抽 2 次（24 分/次）
🎰 活动：周六麦金喜抽奖（进行中）
    消耗 24 积分/次 · 27 档奖品 · 官方未公示概率 ⚠️
📌 第①层 · 无条件回血（积分奖，4 档）：回血 8~28 分/次 → 回收率 33%~117%
📌 第②层 · 有条件变现（立减/抵扣券，9 档）：面值 ¥1~¥8，需下单消费激活
📌 第③层 · 增值型（折扣券，11 档）：4折~6.8折
📌 第④层 · 大奖/权益（3 档）：积分霸王餐 / 麦金卡7天 / 早餐卡7天
──────────────────────────────────────────────
🎯 精算师结论：
    1. 不打算近期消费 → 抽奖≈负期望，不建议
    2. 本来就要吃麦当劳 → 券类可激活，可小抽怡情
    3. 快过期积分 → 抽掉优于烂掉
```

## 声明

本项目从需求分析、MCP 接入、方案设计、代码编写、调试修复到文档产出的全流程均在腾讯 WorkBuddy 中完成。对话过程中的工具调用记录（MCP 请求/响应、文件写入、命令执行）保留在 WorkBuddy 会话中，可供核验。
