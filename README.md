# BinanceEarn — 币安自动化理财 & 空投系统

自动将 BNB 锁入 Binance Simple Earn，被动获得 **利息 + HODLer 空投 + Launchpool 挖矿** 三重收益。

## 工作原理

```
BNB 现货余额 → 自动申购 Simple Earn (Flexible 活期)
                    │
                    ├─ 每日自动计息（2-5% 年化）
                    ├─ HODLer 空投自动获得（5-15% 年化）
                    └─ Launchpool 新项目自动参与（10-30%）
                    │
                    └─ 空投代币到账 → 记录收益 → (可选) 自动卖出
```

## 预期年化收益

| 来源 | 年化 | 风险 |
|------|------|------|
| Simple Earn 利息 | 2-5% | 极低 |
| HODLer 空投 | 5-15% | 极低 |
| Launchpool | 10-30% | 极低 |
| **合计** | **15-50%** | **低（仅 BNB 价格波动）** |

## 项目结构

```
BinanceEarn/
├── binance_earner.py     # 核心：自动理财 + 空投追踪
├── dashboard.py          # Web 看板
├── run.sh                # 交互式启动菜单
├── .env                  # 配置文件（API 密钥 + 参数）
├── .env.example          # 配置模板
├── requirements.txt      # Python 依赖
├── logs/                 # 日志 + 状态
│   ├── state.json        # 实时状态（Dashboard 读取）
│   └── earnings.csv      # 收益记录
├── PLAN.md               # 详细方案设计
└── .gitignore
```

## 快速开始

### 1. 前置准备

- **创建币安 API Key**：[币安](https://www.binance.com) → API 管理 → 创建
  - 权限：✅ 读取 ✅ 现货交易 ❌ 提现
  - 建议设置 IP 白名单
- **确保 BNB 在现货账户**

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
# 编辑 .env，填入你的 API Key 和 Secret
```

### 4. 运行

```bash
# 交互式菜单
./run.sh

# 或直接运行
python3 binance_earner.py --dry-run    # 模拟模式（只查询不操作）
python3 binance_earner.py              # 正式运行
```

## 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `BINANCE_API_KEY` | — | 币安 API Key |
| `BINANCE_API_SECRET` | — | 币安 API Secret |
| `BN_MIN_SUBSCRIBE_BNB` | 0.01 | 最小锁仓 BNB 数量 |
| `BN_RESERVE_BNB` | 0.005 | 保留在现货的 BNB（不全部锁入） |
| `BN_AUTO_SELL_AIRDROP` | false | 空投代币是否自动卖出 |
| `BN_LAUNCHPOOL_SCAN_INTERVAL` | 600 | Launchpool 扫描间隔（秒） |
| `BN_AIRDROP_CHECK_INTERVAL` | 3600 | 空投检查间隔（秒） |
| `BN_BALANCE_SNAPSHOT_INTERVAL` | 300 | 余额快照间隔（秒） |

## Web 看板

启动后访问 `http://服务器IP:8080`，查看：

- 账户资产总览
- Simple Earn 持仓 & 利息
- 空投记录
- 收益统计

## 技术栈

Python 3.12 / asyncio / aiohttp / python-binance / Flask

## 风险提示

- 主要风险为 BNB 价格波动，本金不会因策略操作而损失
- API Key 不开启提现权限，资金安全有保障
- 本系统不构成投资建议
