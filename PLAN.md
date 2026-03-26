# 币安自动化空投 & 理财方案

## 策略概述

将 BNB 自动锁入 Simple Earn，被动获得三重收益：
1. **Simple Earn 利息** — 每日自动计息
2. **HODLer 空投** — 持有 BNB 即自动获得新币空投
3. **Launchpool 挖矿** — 新项目上线时自动参与

核心原则：**零操作被动收益 + 自动化监控通知**

## 可行性分析

### 可通过 API 自动化

| 功能 | API 端点 | 说明 |
|------|----------|------|
| BNB 申购 Simple Earn | `POST /sapi/v1/simple-earn/flexible/subscribe` | 将现货 BNB 自动锁入活期理财 |
| 赎回 Simple Earn | `POST /sapi/v1/simple-earn/flexible/redeem` | 需要时随时取出 |
| 查询持仓 | `GET /sapi/v1/simple-earn/flexible/position` | 监控锁仓状态和利息 |
| 查询产品列表 | `GET /sapi/v1/simple-earn/flexible/list` | 获取可用理财产品 |
| 账户余额 | `GET /api/v3/account` | 实时资产监控 |
| 新币下单 | `POST /api/v3/order` | 空投代币上线后自动卖出 |

### 不可/不建议自动化

| 功能 | 原因 |
|------|------|
| Megadrop Web3 任务 | 需要浏览器钱包签名交互，无公开 API |
| Alpha 空投领取 | 币安明确禁止脚本领取，有反作弊检测 |
| KYC 身份认证 | 一次性手动操作 |

## 预期收益

以 100 USDC ≈ 0.16 BNB 本金为例：

| 收益来源 | 预估年化 | 月收益 | 风险 |
|----------|----------|--------|------|
| Simple Earn 利息 | 2-5% | $0.15-$0.40 | 极低（仅 BNB 价格波动） |
| HODLer 空投 | 5-15% | $0.40-$1.25 | 极低（免费获得） |
| Launchpool 挖矿 | 10-30%（活动期间） | 视项目 | 极低（锁仓即挖） |
| **合计** | **15-50%** | **$1-$4** | **低** |

## 架构设计

```
binance_earner.py (~400 行)
├── EarnManager        — BNB 自动锁仓 & 利息监控
├── LaunchpoolMonitor  — 新项目扫描 & 自动参与通知
├── AirdropTracker     — 空投记录 & 收益统计
├── NewListingSniper   — (可选) 空投币上线自动卖出
└── main()             — 主循环 & 状态输出

配置文件
├── .env               — API Key / Secret / 参数
└── run.sh             — 启动菜单

监控
├── logs/state.json    — 状态文件
├── logs/earnings.csv  — 收益记录
└── dashboard.py       — Web 看板
```

### 流程图

```
启动 → 检查现货 BNB 余额
         │
         ├─ 有空闲 BNB → 自动申购 Simple Earn (Flexible)
         │                    │
         │                    ├─ 每日自动计息 ✓
         │                    ├─ HODLer 空投自动获得 ✓
         │                    └─ Launchpool 自动参与 ✓
         │
         ├─ 每 10 分钟 → 扫描新 Launchpool 项目
         │                    └─ 发现新项目 → 日志通知
         │
         ├─ 每 1 小时 → 检查空投分发记录
         │                    └─ 新空投到账 → 记录 CSV
         │                    └─ (可选) 新币上线 → 自动卖出
         │
         ├─ 每 15 秒 → 写入状态文件 (Dashboard 读取)
         │
         └─ 循环运行
```

## 模块详细设计

### 1. EarnManager

```python
class EarnManager:
    """BNB 自动锁仓管理"""

    async def check_and_subscribe(self):
        """检查现货 BNB 余额，自动申购到 Simple Earn Flexible"""
        # 1. GET /api/v3/account → 获取 BNB 可用余额
        # 2. 如果余额 > 最小锁仓量 (0.01 BNB)
        # 3. GET /sapi/v1/simple-earn/flexible/list → 获取 BNB 产品 ID
        # 4. POST /sapi/v1/simple-earn/flexible/subscribe → 申购
        # 5. 记录操作日志

    async def get_earn_position(self):
        """查询当前 Simple Earn 持仓和累计利息"""
        # GET /sapi/v1/simple-earn/flexible/position

    async def redeem_if_needed(self, amount):
        """需要时赎回 BNB（如参与新 Launchpool）"""
        # POST /sapi/v1/simple-earn/flexible/redeem
```

### 2. LaunchpoolMonitor

```python
class LaunchpoolMonitor:
    """监控新 Launchpool / Megadrop 项目"""

    async def scan_new_projects(self):
        """定期扫描币安公告"""
        # 抓取 binance.com/en/support/announcement 公告
        # 发现新项目 → 记录并通知

    async def check_participation_status(self):
        """检查是否已自动参与（BNB 在 Earn 中即自动参与）"""
```

### 3. AirdropTracker

```python
class AirdropTracker:
    """空投 & 收益追踪"""

    async def check_new_airdrops(self):
        """检查是否有新代币到账"""
        # GET /api/v3/account → 遍历余额
        # 与上次快照对比 → 发现新代币或余额增加
        # 记录到 CSV

    async def calculate_total_earnings(self):
        """统计累计收益"""
        # 汇总: Simple Earn 利息 + 空投价值 + Launchpool 收益
```

### 4. NewListingSniper（可选）

```python
class NewListingSniper:
    """空投代币上线后自动卖出"""

    async def monitor_and_sell(self, token, amount):
        """检测交易对上线 → 自动挂限价卖单"""
        # 1. 轮询 GET /api/v3/exchangeInfo 检查交易对是否存在
        # 2. 交易对上线 → POST /api/v3/order 卖出
        # 3. 建议策略: 上线后观察 5 分钟再卖（避免开盘波动）
```

## 实现计划

| 步骤 | 内容 | 预计耗时 |
|------|------|----------|
| 1 | 创建 `binance_earner.py`，实现 EarnManager（BNB 自动锁仓 + 持仓查询） | 1 小时 |
| 2 | 实现 LaunchpoolMonitor（新项目公告扫描 + 日志通知） | 30 分钟 |
| 3 | 实现 AirdropTracker（余额快照对比 + 新币检测 + CSV 记录） | 30 分钟 |
| 4 | 创建 `.env.example`、`run.sh`、`dashboard.py` | 20 分钟 |
| 5 | 测试运行（dry-run 模式验证 API 连通性） | 10 分钟 |

## 前置条件（用户操作）

1. **创建币安 API Key**
   - 登录 [币安](https://www.binance.com) → API 管理 → 创建 API
   - 权限：✅ 读取 ✅ 现货交易 ❌ 提现（安全起见不开）
   - 建议设置 IP 白名单为服务器 IP
2. **确保 BNB 在现货账户**
   - 可用 USDC 先在币安买入 BNB
3. **将 API Key 和 Secret 填入 `.env`**

## 风险说明

| 风险 | 等级 | 应对 |
|------|------|------|
| BNB 价格下跌 | 中 | 长期持有 + 空投收益对冲 |
| API Key 泄露 | 低 | IP 白名单 + 不开提现权限 |
| 空投代币归零 | 低 | 空投是免费的，不影响本金 |
| 币安系统风险 | 极低 | 头部交易所，安全性高 |
| Launchpool 收益不及预期 | 低 | 本金不损失，只是收益波动 |
