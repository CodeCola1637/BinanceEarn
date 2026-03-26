# BinanceEarn — 币安全自动理财系统

一次配置，永久运行。自动将 BNB 锁入 Simple Earn，被动获得利息 + HODLer 空投，空投代币自动卖出变现。

## 工作原理

```
启动后完全无人值守：

  BNB → 自动锁入 Simple Earn
           │
           ├─ 利息每日自动到账
           ├─ HODLer 空投自动到账
           │
           └─ 新代币检测 → 自动市价卖出 → USDC 回账
                                            └─ (可选) 自动买 BNB → 复利
```

## 自动化程度

| 功能 | 自动化 | 说明 |
|------|--------|------|
| BNB 锁仓 | ✅ 全自动 | 检测到现货余额自动申购 |
| Earn 利息 | ✅ 全自动 | 币安平台每日自动计息 |
| HODLer 空投 | ✅ 全自动 | BNB 在 Earn 中即自动获得 |
| 空投代币卖出 | ✅ 全自动 | 检测到新币自动市价卖出 |
| Launchpool 通知 | ✅ 仅日志 | 新项目写入日志，不操作 |
| **人工介入** | **无** | **启动后零操作** |

## 预期年化收益

| 来源 | 年化 |
|------|------|
| Simple Earn 利息 | 2-5% |
| HODLer 空投 | 5-15% |
| 复利（可选） | +1-3% |
| **合计** | **8-23%** |

## 快速开始

### 1. 前置准备（一次性）

- 创建币安 API Key：权限 ✅读取 ✅现货交易 ❌提现
- 现货账户有 BNB

### 2. 安装

```bash
cd /home/ubuntu/BinanceEarn
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. 配置

```bash
cp .env.example .env
# 编辑 .env，填入 API Key 和 Secret
```

### 4. 运行

```bash
./run.sh
# 选 1 = Dry-Run（只查询不操作）
# 选 3 = 正式运行
```

## 项目结构

```
BinanceEarn/
├── binance_earner.py     # 核心：自动锁仓 + 空投卖出
├── dashboard.py          # Web 看板
├── run.sh                # 启动菜单
├── .env                  # 配置（API 密钥 + 参数）
├── .env.example          # 配置模板
├── requirements.txt      # Python 依赖
├── logs/
│   ├── state.json        # 实时状态
│   ├── earnings.csv      # 收益记录
│   └── balances.json     # 余额快照
├── PLAN.md               # 详细方案设计
└── .gitignore
```

## 技术栈

Python 3.12 / asyncio / aiohttp / Flask

## 风险提示

主要风险为 BNB 价格波动，本金不会因策略操作而损失。API Key 不开启提现权限，资金安全有保障。
