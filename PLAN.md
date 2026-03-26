# 币安自动化理财方案（模式 A：完全无人值守）

## 策略概述

将 BNB 自动锁入 Simple Earn，**启动后零人工介入**，被动获得三重收益：
1. **Simple Earn 利息** — 币安自动计息，每日到账
2. **HODLer 空投** — BNB 在 Earn 中即自动获得新币
3. **空投代币自动变现** — 检测到新代币到账后自动卖出换回 USDC

核心原则：**一次配置，永久运行，完全无人值守**

## 人工介入点

| 操作 | 时机 | 频率 |
|------|------|------|
| 创建币安 API Key | 初始化 | 一次性 |
| 买入 BNB 到现货账户 | 初始化 | 一次性 |
| 填写 `.env` 并启动 | 初始化 | 一次性 |
| **以上完成后，无需任何操作** | — | — |

## 自动化流程

```
启动
  │
  ├─ ① 自动锁仓（每 5 分钟检查一次）
  │     └─ 现货 BNB > 阈值 → 自动申购 Simple Earn Flexible
  │     └─ 已全部锁仓 → 跳过，等待下次检查
  │
  ├─ ② 收益自动累积（币安平台侧）
  │     ├─ Simple Earn 利息 → 每日自动到账
  │     └─ HODLer 空投 → 符合条件时自动分发到现货账户
  │
  ├─ ③ 空投检测 & 自动卖出（每 30 分钟检查一次）
  │     ├─ 扫描现货余额，与上次快照对比
  │     ├─ 发现新代币或余额增加 → 记录到 CSV
  │     ├─ 检查该代币是否有 USDC 交易对
  │     │     ├─ 有交易对 → 自动市价卖出 → USDC 回账
  │     │     └─ 无交易对 → 检查 USDT/BTC 对 → 卖出 → 换回 USDC
  │     │     └─ 均无交易对 → 持有等待，下次再检查
  │     └─ 卖出所得 USDC → (可选) 自动买入 BNB → 再锁仓（复利）
  │
  ├─ ④ 公告监控（每 30 分钟检查一次，仅日志记录）
  │     └─ 扫描币安公告 → Launchpool/空投相关 → 写入日志
  │     └─ 不执行任何操作，仅供 Dashboard 展示
  │
  ├─ ⑤ 状态输出（每 30 秒）
  │     └─ 写入 state.json → Dashboard 读取展示
  │
  └─ 循环运行（永不停止）
```

## 可行性分析

### 完全自动化（API 支持）

| 功能 | API | 自动化程度 |
|------|-----|-----------|
| BNB 锁仓 | `POST /sapi/v1/simple-earn/flexible/subscribe` | 100% 自动 |
| BNB 赎回 | `POST /sapi/v1/simple-earn/flexible/redeem` | 100% 自动 |
| Earn 持仓查询 | `GET /sapi/v1/simple-earn/flexible/position` | 100% 自动 |
| 现货余额查询 | `GET /api/v3/account` | 100% 自动 |
| 交易对信息 | `GET /api/v3/exchangeInfo` | 100% 自动 |
| 市价卖出代币 | `POST /api/v3/order` (MARKET) | 100% 自动 |
| 利息到账 | 币安平台自动 | 无需调用 |
| HODLer 空投 | 币安平台自动 | 无需调用 |

### 不可自动化（不实现）

| 功能 | 原因 |
|------|------|
| Megadrop Web3 任务 | 需浏览器钱包交互 |
| Alpha 空投领取 | 币安反作弊检测 |
| Launchpool 手动申购 | 部分项目需 App 操作，收益占比小，不影响核心策略 |

## 预期收益

以 100 USDC ≈ 0.16 BNB 本金为例：

| 收益来源 | 预估年化 | 自动化 |
|----------|----------|--------|
| Simple Earn 利息 | 2-5% | ✅ 全自动 |
| HODLer 空投 | 5-15% | ✅ 全自动（含自动卖出） |
| 空投复利（卖出 → 买 BNB → 再锁仓） | +1-3% | ✅ 全自动（可选） |
| **合计** | **8-23%** | **100% 自动** |

> 注：去掉了 Launchpool 手动参与部分的收益预期，仅保留完全自动化的收益。

## 架构设计

```
binance_earner.py (~350 行)
├── BinanceClient      — API 封装（签名、请求、错误处理）
├── EarnManager        — BNB 自动锁仓 & Earn 持仓查询
├── AirdropTracker     — 余额快照 & 新代币检测 & 自动卖出
├── AnnouncementWatch  — 公告扫描（仅日志，不操作）
└── main()             — 主循环 & 状态输出

dashboard.py (~200 行)
└── Flask Web 看板     — 资产、收益、空投记录、公告

配置
├── .env               — API 凭证 + 策略参数
└── run.sh             — 交互式启动菜单

数据
├── logs/state.json    — 实时状态
├── logs/earnings.csv  — 收益记录
└── logs/balances.json — 余额快照（对比用）
```

## 模块详细设计

### 1. BinanceClient

```python
class BinanceClient:
    """币安 API 封装，处理签名和错误"""

    def __init__(self, api_key, api_secret):
        self.api_key = api_key
        self.api_secret = api_secret

    async def _request(self, method, path, params=None, signed=True):
        """统一请求方法，自动签名、重试、错误处理"""

    async def get_account(self) -> dict:
        """GET /api/v3/account"""

    async def get_earn_products(self, asset="BNB") -> list:
        """GET /sapi/v1/simple-earn/flexible/list"""

    async def subscribe_earn(self, product_id, amount) -> dict:
        """POST /sapi/v1/simple-earn/flexible/subscribe"""

    async def get_earn_position(self, asset="BNB") -> dict:
        """GET /sapi/v1/simple-earn/flexible/position"""

    async def get_exchange_info(self, symbol=None) -> dict:
        """GET /api/v3/exchangeInfo"""

    async def market_sell(self, symbol, quantity) -> dict:
        """POST /api/v3/order (MARKET SELL)"""
```

### 2. EarnManager

```python
class EarnManager:
    """BNB 自动锁仓管理 — 全自动，无需人工"""

    async def ensure_subscribed(self):
        """确保 BNB 已锁入 Simple Earn"""
        # 1. 查询现货 BNB 余额
        # 2. 扣除 reserve 后，剩余全部锁入
        # 3. 已全部锁入 → 跳过
        # 4. 记录日志

    async def get_status(self) -> dict:
        """获取 Earn 持仓状态（供 Dashboard 展示）"""
        # 返回: 锁仓数量、累计利息、年化利率
```

### 3. AirdropTracker

```python
class AirdropTracker:
    """空投检测 + 自动卖出 — 全自动，无需人工"""

    async def scan_and_sell(self):
        """扫描余额变化，检测新空投，自动卖出"""
        # 1. GET /api/v3/account → 当前余额快照
        # 2. 与上次快照对比 → 新增代币或余额增加
        # 3. 过滤: 排除 BNB/USDC/USDT 等基础币种
        # 4. 对每个新代币:
        #    a. 查询 exchangeInfo 是否有 USDC/USDT 交易对
        #    b. 有 → 市价卖出 → 记录到 CSV
        #    c. 无 → 标记为 "待上线"，下次再查
        # 5. 保存新快照

    async def get_earnings_summary(self) -> dict:
        """统计累计收益（供 Dashboard 展示）"""
        # 读取 CSV，汇总各代币卖出金额
```

### 4. AnnouncementWatch

```python
class AnnouncementWatch:
    """公告监控 — 仅记录日志，不执行操作"""

    async def check_announcements(self):
        """扫描币安公告页面"""
        # 1. 请求 binance.com 公告 API
        # 2. 筛选关键词: Launchpool, Airdrop, HODLer
        # 3. 与已知列表对比 → 新公告 → 写入日志
        # 4. 不执行任何交易操作
```

## 配置参数

```env
# ── 币安 API ─────────────────────────────
BINANCE_API_KEY=
BINANCE_API_SECRET=

# ── 锁仓参数 ─────────────────────────────
BN_MIN_SUBSCRIBE_BNB=0.01       # 最小锁仓量
BN_RESERVE_BNB=0.005            # 保留现货 BNB（不全部锁入）
BN_AUTO_SELL_AIRDROP=true       # 空投代币自动卖出（模式A默认开启）
BN_AUTO_COMPOUND=false          # 卖出所得自动买 BNB 复利（可选）

# ── 检查频率 ─────────────────────────────
BN_EARN_CHECK_INTERVAL=300      # 锁仓检查间隔（秒）
BN_AIRDROP_CHECK_INTERVAL=1800  # 空投检查间隔（秒）
BN_ANNOUNCE_CHECK_INTERVAL=1800 # 公告扫描间隔（秒）
BN_STATE_WRITE_INTERVAL=30      # 状态文件写入间隔（秒）

# ── Dashboard ────────────────────────────
DASHBOARD_PORT=8080
```

## 实现计划

| 步骤 | 内容 | 预计耗时 |
|------|------|----------|
| 1 | `BinanceClient` — API 封装（签名、请求、重试） | 30 分钟 |
| 2 | `EarnManager` — BNB 自动锁仓 + 持仓查询 | 30 分钟 |
| 3 | `AirdropTracker` — 余额快照 + 新币检测 + 自动卖出 | 40 分钟 |
| 4 | `AnnouncementWatch` — 公告扫描（仅日志） | 20 分钟 |
| 5 | 主循环 + 状态输出 + dry-run 模式 | 20 分钟 |
| 6 | `dashboard.py` — Web 看板 | 30 分钟 |
| 7 | 更新 `.env.example`、`run.sh` | 10 分钟 |
| 8 | Dry-run 测试 | 10 分钟 |

## 前置条件（一次性）

1. **创建币安 API Key**
   - 币安 → API 管理 → 创建
   - 权限：✅ 读取 ✅ 现货交易 ❌ 提现
   - 建议设置 IP 白名单
2. **BNB 在现货账户**
3. **填写 `.env`**

## 风险说明

| 风险 | 等级 | 应对 |
|------|------|------|
| BNB 价格下跌 | 中 | 空投收益对冲部分跌幅 |
| API Key 泄露 | 低 | IP 白名单 + 不开提现 |
| 空投代币归零 | 极低 | 自动卖出，不长期持有 |
| 自动卖出价格不佳 | 低 | 市价单即时成交，避免挂单风险 |
| 币安系统风险 | 极低 | 头部交易所 |
