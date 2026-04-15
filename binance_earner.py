#!/usr/bin/env python3
"""BinanceEarn — 全自动 BNB 理财 & 空投系统（模式 A：完全无人值守）"""

import argparse
import asyncio
import csv
import hashlib
import hmac
import json
import logging
import logging.handlers
import os
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
from dotenv import load_dotenv

load_dotenv()

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
STATE_FILE = LOG_DIR / "state.json"
EARNINGS_CSV = LOG_DIR / "earnings.csv"
BALANCES_FILE = LOG_DIR / "balances.json"
SEEN_IDS_FILE = LOG_DIR / "seen_announcements.json"

_file_handler = logging.handlers.RotatingFileHandler(
    LOG_DIR / "earner.log", maxBytes=5 * 1024 * 1024, backupCount=3,
)
_file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
_handlers: list[logging.Handler] = [_file_handler]
if sys.stdout.isatty():
    _handlers.append(logging.StreamHandler())
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    handlers=_handlers)
log = logging.getLogger("binance_earner")

API_KEY = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")

MIN_SUBSCRIBE_BNB = float(os.getenv("BN_MIN_SUBSCRIBE_BNB", "0.01"))
RESERVE_BNB = float(os.getenv("BN_RESERVE_BNB", "0.005"))
AUTO_SELL = os.getenv("BN_AUTO_SELL_AIRDROP", "true").lower() == "true"
AUTO_COMPOUND = os.getenv("BN_AUTO_COMPOUND", "false").lower() == "true"

EARN_CHECK_INTERVAL = int(os.getenv("BN_EARN_CHECK_INTERVAL", "1800"))
AIRDROP_CHECK_INTERVAL = int(os.getenv("BN_AIRDROP_CHECK_INTERVAL", "120"))
ANNOUNCE_CHECK_INTERVAL = int(os.getenv("BN_ANNOUNCE_CHECK_INTERVAL", "1800"))
STATE_WRITE_INTERVAL = int(os.getenv("BN_STATE_WRITE_INTERVAL", "120"))

# Locked Earn: APR 高于此阈值时优先选 Locked 产品
LOCKED_APR_THRESHOLD = float(os.getenv("BN_LOCKED_APR_THRESHOLD", "0.005"))
PREFER_LOCKED = os.getenv("BN_PREFER_LOCKED", "true").lower() == "true"

# Webhook 通知 (企业微信 Bot / 自定义)
WEBHOOK_URL = os.getenv("BN_WEBHOOK_URL", "")

BASE_URL = "https://api.binance.com"
SKIP_ASSETS = {"BNB", "USDC", "USDT", "BUSD", "FDUSD", "USD", "EUR",
               "LDBNB", "WBNB", "BFUSD"}


# ═══════════════════════════════════════════════
#  Notifier — 通知推送
# ═══════════════════════════════════════════════

class Notifier:
    """通过 Webhook（企业微信 Bot / 飞书 / 自定义）推送关键事件"""

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.url = WEBHOOK_URL

    async def send(self, title: str, body: str, level: str = "info"):
        if not self.url:
            return
        emoji = {"info": "ℹ️", "warn": "⚠️", "success": "✅", "alert": "🚨"}.get(level, "📌")
        text = f"{emoji} **{title}**\n{body}"
        try:
            payload: dict
            if "qyapi.weixin" in self.url:
                payload = {"msgtype": "markdown", "markdown": {"content": text}}
            elif "feishu" in self.url or "larksuite" in self.url:
                payload = {"msg_type": "text", "content": {"text": f"{emoji} {title}\n{body}"}}
            else:
                payload = {"text": text}
            async with self.session.post(self.url, json=payload,
                                         timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    log.warning("Webhook 推送失败: HTTP %d", resp.status)
        except Exception as e:
            log.debug("Webhook 异常: %s", e)

    async def notify_airdrop(self, asset: str, amount: float, sold_quote: str = "",
                             sold_amount: str = ""):
        lines = [f"代币: **{asset}**", f"数量: {amount:.8f}"]
        if sold_quote:
            lines.append(f"已卖出: {sold_amount} {sold_quote}")
        await self.send("空投到账", "\n".join(lines), "success")

    async def notify_announcement(self, title: str):
        await self.send("新公告", title, "info")

    async def notify_error(self, msg: str):
        await self.send("系统异常", msg, "alert")

    async def notify_locked_subscribe(self, amount: float, product_id: str,
                                      apr: float, duration: int):
        await self.send("Locked Earn 锁仓",
                        f"金额: {amount:.6f} BNB\n产品: {product_id}\n"
                        f"APR: {apr:.2f}%\n锁定期: {duration}天", "success")


# ═══════════════════════════════════════════════
#  BinanceClient — API 封装
# ═══════════════════════════════════════════════

class BinanceClient:
    def __init__(self, api_key: str, api_secret: str, session: aiohttp.ClientSession):
        self.api_key = api_key
        self.api_secret = api_secret
        self.session = session

    def _sign(self, params: dict) -> dict:
        params["timestamp"] = int(time.time() * 1000)
        qs = urlencode(params)
        sig = hmac.new(self.api_secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        params["signature"] = sig
        return params

    def _headers(self) -> dict:
        return {"X-MBX-APIKEY": self.api_key}

    async def _request(self, method: str, path: str, params: dict | None = None,
                       signed: bool = True) -> dict:
        params = dict(params or {})
        if signed:
            params = self._sign(params)
        url = f"{BASE_URL}{path}"
        for attempt in range(3):
            try:
                async with self.session.request(method, url, params=params,
                                                headers=self._headers()) as resp:
                    data = await resp.json()
                    if resp.status != 200:
                        code = data.get("code", resp.status)
                        msg = data.get("msg", "unknown")
                        log.warning("API %s %s → %s: %s", method, path, code, msg)
                        if resp.status == 429:
                            await asyncio.sleep(10 * (attempt + 1))
                            continue
                        return {"_error": True, "code": code, "msg": msg}
                    return data
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning("API request failed (attempt %d): %s", attempt + 1, e)
                await asyncio.sleep(2 ** attempt)
        return {"_error": True, "code": -1, "msg": "max retries exceeded"}

    async def get_account(self) -> dict:
        return await self._request("GET", "/api/v3/account")

    async def get_earn_flexible_list(self, asset: str = "BNB") -> dict:
        return await self._request("GET", "/sapi/v1/simple-earn/flexible/list",
                                   {"asset": asset, "size": 100})

    async def get_earn_locked_list(self, asset: str = "BNB") -> dict:
        return await self._request("GET", "/sapi/v1/simple-earn/locked/list",
                                   {"asset": asset, "size": 100})

    async def subscribe_earn_flexible(self, product_id: str, amount: float) -> dict:
        return await self._request("POST", "/sapi/v1/simple-earn/flexible/subscribe",
                                   {"productId": product_id, "amount": f"{amount:.8f}"})

    async def subscribe_earn_locked(self, product_id: str, amount: float) -> dict:
        return await self._request("POST", "/sapi/v1/simple-earn/locked/subscribe",
                                   {"productId": product_id, "amount": f"{amount:.8f}"})

    async def get_earn_position(self, asset: str = "") -> dict:
        params = {"size": 100}
        if asset:
            params["asset"] = asset
        return await self._request("GET", "/sapi/v1/simple-earn/flexible/position", params)

    async def get_earn_locked_position(self, asset: str = "") -> dict:
        params = {"size": 100}
        if asset:
            params["asset"] = asset
        return await self._request("GET", "/sapi/v1/simple-earn/locked/position", params)

    async def get_exchange_info(self) -> dict:
        return await self._request("GET", "/api/v3/exchangeInfo", signed=False)

    async def market_sell(self, symbol: str, quantity: str) -> dict:
        return await self._request("POST", "/api/v3/order", {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": quantity,
        })

    async def market_buy(self, symbol: str, quote_qty: str) -> dict:
        return await self._request("POST", "/api/v3/order", {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": quote_qty,
        })

    async def get_ticker_price(self, symbol: str) -> dict:
        return await self._request("GET", "/api/v3/ticker/price",
                                   {"symbol": symbol}, signed=False)


# ═══════════════════════════════════════════════
#  EarnManager — BNB 自动锁仓 (Flexible + Locked)
# ═══════════════════════════════════════════════

class EarnManager:
    def __init__(self, client: BinanceClient, dry_run: bool,
                 notifier: "Notifier | None" = None):
        self.client = client
        self.dry_run = dry_run
        self.notifier = notifier
        self.earn_position: dict = {}
        self.locked_positions: list[dict] = []
        self.last_subscribe_time: float = 0

    async def ensure_subscribed(self) -> str | None:
        """检查现货 BNB，优先锁入 Locked（APR 更高），否则 Flexible"""
        acct = await self.client.get_account()
        if acct.get("_error"):
            return f"账户查询失败: {acct.get('msg')}"

        bnb_free = 0.0
        for b in acct.get("balances", []):
            if b["asset"] == "BNB":
                bnb_free = float(b["free"])
                break

        available = bnb_free - RESERVE_BNB
        if available < MIN_SUBSCRIBE_BNB:
            log.debug("现货 BNB=%.6f，可锁仓=%.6f < 阈值 %.4f，跳过",
                      bnb_free, max(0, available), MIN_SUBSCRIBE_BNB)
            return None

        # 尝试 Locked Earn（APR 更高）
        if PREFER_LOCKED:
            result = await self._try_locked(available)
            if result is True:
                return None
            if isinstance(result, str):
                log.info("Locked Earn 未命中: %s，回退到 Flexible", result)

        # 回退到 Flexible Earn
        return await self._subscribe_flexible(available)

    async def _try_locked(self, available: float) -> bool | str:
        """尝试订阅 Locked 产品，成功返回 True，否则返回原因字符串"""
        locked = await self.client.get_earn_locked_list("BNB")
        if locked.get("_error"):
            return f"Locked 列表查询失败: {locked.get('msg')}"

        rows = locked.get("rows", [])
        if not rows:
            return "无 BNB Locked 产品"

        # 过滤可购买 + APR 高于阈值的产品，按 APR 降序
        candidates = []
        for r in rows:
            apr = float(r.get("annualPercentageRate", "0"))
            status = r.get("status", "")
            can_purchase = r.get("canPurchase", False)
            min_amt = float(r.get("minPurchaseAmount", "999999"))
            if status == "PURCHASING" and can_purchase and apr >= LOCKED_APR_THRESHOLD and available >= min_amt:
                candidates.append(r)

        if not candidates:
            return f"无满足条件的 Locked 产品 (阈值 APR>{LOCKED_APR_THRESHOLD*100:.1f}%)"

        best = max(candidates, key=lambda r: float(r.get("annualPercentageRate", "0")))
        product_id = best.get("projectId", best.get("productId", ""))
        apr = float(best.get("annualPercentageRate", "0")) * 100
        duration = int(best.get("duration", 0))
        min_amount = float(best.get("minPurchaseAmount", "0.001"))
        amount = max(min_amount, available)

        log.info("准备 Locked 锁仓 %.6f BNB → %s (APR=%.2f%%, %d天, 最小 %.6f)",
                 amount, product_id, apr, duration, min_amount)

        if self.dry_run:
            log.info("[DRY-RUN] 跳过 Locked 锁仓")
            return True

        result = await self.client.subscribe_earn_locked(product_id, amount)
        if result.get("_error"):
            return f"Locked 锁仓失败: {result.get('msg')}"

        self.last_subscribe_time = time.time()
        log.info("✅ Locked 锁仓成功 %.6f BNB, purchaseId=%s, APR=%.2f%%, 期限=%d天",
                 amount, result.get("purchaseId"), apr, duration)

        if self.notifier:
            await self.notifier.notify_locked_subscribe(amount, product_id, apr, duration)
        return True

    async def _subscribe_flexible(self, available: float) -> str | None:
        """回退：订阅 Flexible 产品"""
        products = await self.client.get_earn_flexible_list("BNB")
        if products.get("_error"):
            return f"Flexible 列表查询失败: {products.get('msg')}"

        rows = products.get("rows", [])
        if not rows:
            return "未找到 BNB Flexible Earn 产品"

        product = max(rows, key=lambda r: float(r.get("latestAnnualPercentageRate", "0")))
        product_id = product.get("productId")
        apr = float(product.get("latestAnnualPercentageRate", "0")) * 100
        min_amount = float(product.get("minPurchaseAmount", "0.001"))
        amount = max(min_amount, available)

        log.info("准备 Flexible 锁仓 %.6f BNB → 产品 %s (APR=%.2f%%, 最小 %.6f)",
                 amount, product_id, apr, min_amount)

        if self.dry_run:
            log.info("[DRY-RUN] 跳过 Flexible 锁仓")
            return None

        result = await self.client.subscribe_earn_flexible(product_id, amount)
        if result.get("_error"):
            return f"Flexible 锁仓失败: {result.get('msg')}"

        self.last_subscribe_time = time.time()
        log.info("✅ Flexible 锁仓成功 %.6f BNB, purchaseId=%s", amount, result.get("purchaseId"))
        return None

    async def refresh_position(self):
        """刷新 Earn 持仓信息 (Flexible + Locked)"""
        pos = await self.client.get_earn_position("BNB")
        if pos.get("_error"):
            log.warning("Flexible 持仓查询失败: %s", pos.get("msg"))
        else:
            rows = pos.get("rows", [])
            if rows:
                r = rows[0]
                self.earn_position = {
                    "type": "flexible",
                    "asset": r.get("asset", "BNB"),
                    "totalAmount": r.get("totalAmount", "0"),
                    "latestAnnualPercentageRate": r.get("latestAnnualPercentageRate", "0"),
                    "totalRewards": r.get("totalRewards", "0"),
                    "yesterdayRealTimeRewards": r.get("yesterdayRealTimeRewards", "0"),
                    "cumulativeBonusRewards": r.get("cumulativeBonusRewards", "0"),
                    "cumulativeRealTimeRewards": r.get("cumulativeRealTimeRewards", "0"),
                    "cumulativeTotalRewards": r.get("cumulativeTotalRewards", "0"),
                }
                log.info("Flexible 持仓: %s BNB, APR=%.2f%%, 累计收益=%s",
                         self.earn_position["totalAmount"],
                         float(self.earn_position["latestAnnualPercentageRate"]) * 100,
                         self.earn_position["cumulativeTotalRewards"])
            else:
                self.earn_position = {}

        locked_pos = await self.client.get_earn_locked_position("BNB")
        if locked_pos.get("_error"):
            log.debug("Locked 持仓查询失败: %s", locked_pos.get("msg"))
        else:
            self.locked_positions = []
            for r in locked_pos.get("rows", []):
                lp = {
                    "type": "locked",
                    "asset": r.get("asset", "BNB"),
                    "amount": r.get("amount", "0"),
                    "annualPercentageRate": r.get("annualPercentageRate", "0"),
                    "duration": r.get("duration", 0),
                    "accrualDays": r.get("accrualDays", 0),
                    "rewardsAmount": r.get("rewardsAmount", "0"),
                }
                self.locked_positions.append(lp)
            if self.locked_positions:
                total_locked = sum(float(p["amount"]) for p in self.locked_positions)
                log.info("Locked 持仓: %.6f BNB (%d 笔)", total_locked, len(self.locked_positions))


# ═══════════════════════════════════════════════
#  AirdropTracker — 空投检测 & 自动卖出
# ═══════════════════════════════════════════════

class AirdropTracker:
    def __init__(self, client: BinanceClient, dry_run: bool,
                 notifier: "Notifier | None" = None):
        self.client = client
        self.dry_run = dry_run
        self.notifier = notifier
        self.previous_balances: dict[str, float] = {}
        self.trading_pairs: dict[str, list[str]] = {}
        self.sell_history: list[dict] = []
        self._load_balances()

    def _load_balances(self):
        if BALANCES_FILE.exists():
            try:
                self.previous_balances = json.loads(BALANCES_FILE.read_text())
                log.info("加载余额快照: %d 个币种", len(self.previous_balances))
            except Exception:
                self.previous_balances = {}

    def _save_balances(self):
        BALANCES_FILE.write_text(json.dumps(self.previous_balances, indent=2))

    async def _refresh_trading_pairs(self):
        info = await self.client.get_exchange_info()
        if info.get("_error"):
            log.warning("交易对查询失败: %s", info.get("msg"))
            return
        self.trading_pairs.clear()
        for s in info.get("symbols", []):
            if s.get("status") != "TRADING":
                continue
            base = s["baseAsset"]
            quote = s["quoteAsset"]
            symbol = s["symbol"]
            if quote in ("USDC", "USDT", "BTC"):
                self.trading_pairs.setdefault(base, []).append({
                    "symbol": symbol,
                    "quote": quote,
                    "filters": {f["filterType"]: f for f in s.get("filters", [])},
                })

    def _format_quantity(self, amount: float, pair_info: dict) -> str | None:
        lot_filter = pair_info["filters"].get("LOT_SIZE", {})
        min_qty = float(lot_filter.get("minQty", "0.001"))
        step = float(lot_filter.get("stepSize", "0.001"))
        if amount < min_qty:
            return None
        precision = max(0, len(str(step).rstrip('0').split('.')[-1]))
        qty = int(amount / step) * step
        if qty < min_qty:
            return None
        return f"{qty:.{precision}f}"

    async def scan_and_sell(self) -> list[dict]:
        """扫描余额变化，检测空投，自动卖出"""
        acct = await self.client.get_account()
        if acct.get("_error"):
            log.warning("余额查询失败: %s", acct.get("msg"))
            return []

        current = {}
        for b in acct.get("balances", []):
            total = float(b["free"]) + float(b["locked"])
            if total > 0:
                current[b["asset"]] = total

        new_airdrops = []
        for asset, amount in current.items():
            if asset in SKIP_ASSETS:
                continue
            prev = self.previous_balances.get(asset, 0)
            diff = amount - prev
            if diff > 0 and (prev == 0 or diff / max(prev, 1e-12) > 0.001):
                new_airdrops.append({
                    "asset": asset,
                    "amount": diff,
                    "total": amount,
                    "is_new": prev == 0,
                    "time": datetime.now(timezone.utc).isoformat(),
                })

        self.previous_balances = current
        self._save_balances()

        if not new_airdrops:
            log.info("余额扫描完成，无新增代币（%d 个币种）", len(current))
            return []

        for ad in new_airdrops:
            tag = "新币" if ad["is_new"] else "增加"
            log.info("🎁 检测到%s: %s +%.8f (总 %.8f)",
                     tag, ad["asset"], ad["amount"], ad["total"])
            if ad["asset"] not in SKIP_ASSETS:
                self._record_earning(ad)

        if AUTO_SELL:
            await self._refresh_trading_pairs()
            for ad in new_airdrops:
                await self._try_sell(ad)

        return new_airdrops

    async def _try_sell(self, airdrop: dict):
        """尝试卖出空投代币，然后复利买入 BNB"""
        asset = airdrop["asset"]
        amount = airdrop["total"]

        pairs = self.trading_pairs.get(asset, [])
        if not pairs:
            log.info("⏳ %s 暂无交易对，持有等待", asset)
            if self.notifier:
                await self.notifier.notify_airdrop(asset, airdrop["amount"])
            return

        for pinfo in sorted(pairs, key=lambda p: {"USDC": 0, "USDT": 1, "BTC": 2}.get(p["quote"], 9)):
            qty_str = self._format_quantity(amount, pinfo)
            if not qty_str:
                continue

            symbol = pinfo["symbol"]
            log.info("准备卖出 %s %s → %s", qty_str, asset, symbol)

            if self.dry_run:
                log.info("[DRY-RUN] 跳过实际卖出")
                if self.notifier:
                    await self.notifier.notify_airdrop(asset, airdrop["amount"])
                return

            result = await self.client.market_sell(symbol, qty_str)
            if result.get("_error"):
                log.warning("卖出 %s 失败: %s", symbol, result.get("msg"))
                continue

            filled_qty = result.get("executedQty", "0")
            filled_quote = result.get("cummulativeQuoteQty", "0")
            quote_asset = pinfo["quote"]
            log.info("✅ 卖出成功: %s %s → %s %s",
                     filled_qty, asset, filled_quote, quote_asset)

            self.sell_history.append({
                "time": datetime.now(timezone.utc).isoformat(),
                "asset": asset,
                "quantity": filled_qty,
                "quote_asset": quote_asset,
                "quote_amount": filled_quote,
                "symbol": symbol,
            })
            self._record_sell(asset, filled_qty, quote_asset, filled_quote)

            if self.notifier:
                await self.notifier.notify_airdrop(asset, airdrop["amount"],
                                                   quote_asset, filled_quote)

            if AUTO_COMPOUND:
                await self._compound_to_bnb(quote_asset, float(filled_quote))
            return

        log.info("⏳ %s 余额不满足最小交易量，持有等待", asset)

    async def _compound_to_bnb(self, quote_asset: str, quote_amount: float):
        """将卖出所得转换为 BNB（复利）"""
        if quote_amount < 1.0:
            log.info("复利: %s %.4f 金额太小，跳过", quote_asset, quote_amount)
            return

        buy_symbol = f"BNB{quote_asset}"
        log.info("复利: 用 %.4f %s 买入 BNB (%s)", quote_amount, quote_asset, buy_symbol)

        if self.dry_run:
            log.info("[DRY-RUN] 跳过复利买入")
            return

        result = await self.client.market_buy(buy_symbol, f"{quote_amount:.2f}")
        if result.get("_error"):
            log.warning("复利买 BNB 失败: %s", result.get("msg"))
            return

        bnb_bought = result.get("executedQty", "0")
        spent = result.get("cummulativeQuoteQty", "0")
        log.info("✅ 复利成功: %s %s → %s BNB", spent, quote_asset, bnb_bought)
        self._record_compound(quote_asset, spent, bnb_bought)

    def _record_earning(self, airdrop: dict):
        is_new = not EARNINGS_CSV.exists()
        with open(EARNINGS_CSV, "a", newline="") as f:
            w = csv.writer(f)
            if is_new:
                w.writerow(["time", "type", "asset", "amount", "total",
                            "sold_to", "sold_amount"])
            w.writerow([airdrop["time"], "airdrop", airdrop["asset"],
                        f"{airdrop['amount']:.8f}", f"{airdrop['total']:.8f}", "", ""])

    def _record_sell(self, asset: str, qty: str, quote: str, quote_amt: str):
        with open(EARNINGS_CSV, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow([datetime.now(timezone.utc).isoformat(), "sell",
                        asset, qty, "", quote, quote_amt])

    def _record_compound(self, quote: str, spent: str, bnb_qty: str):
        with open(EARNINGS_CSV, "a", newline="") as f:
            w = csv.writer(f)
            w.writerow([datetime.now(timezone.utc).isoformat(), "compound",
                        "BNB", bnb_qty, "", quote, spent])


# ═══════════════════════════════════════════════
#  AnnouncementWatch — 公告监控 + 通知
# ═══════════════════════════════════════════════

class AnnouncementWatch:
    BASE_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/catalog/list/query"
    CATALOG_IDS = [48, 128]
    KEYWORDS = re.compile(r"launchpool|hodler|airdrop", re.IGNORECASE)

    def __init__(self, session: aiohttp.ClientSession,
                 notifier: "Notifier | None" = None):
        self.session = session
        self.notifier = notifier
        self.seen_ids: set[int] = set()
        self.recent: list[dict] = []
        self._load_seen_ids()

    def _load_seen_ids(self):
        if SEEN_IDS_FILE.exists():
            try:
                self.seen_ids = set(json.loads(SEEN_IDS_FILE.read_text()))
                log.info("加载已知公告: %d 条", len(self.seen_ids))
            except Exception:
                self.seen_ids = set()

    def _save_seen_ids(self):
        SEEN_IDS_FILE.write_text(json.dumps(sorted(self.seen_ids)[-200:]))

    async def check(self):
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        new_count = 0
        for cid in self.CATALOG_IDS:
            try:
                url = f"{self.BASE_URL}?catalogId={cid}&pageNo=1&pageSize=20"
                async with self.session.get(url, headers=headers,
                                            timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        continue
                    data = await resp.json()

                articles = data.get("data", {}).get("articles", [])
                for art in articles:
                    aid = art.get("id", 0)
                    title = art.get("title", "")
                    if aid in self.seen_ids:
                        continue
                    self.seen_ids.add(aid)
                    if self.KEYWORDS.search(title):
                        log.info("📢 新公告: %s", title)
                        self.recent.append({
                            "id": aid,
                            "title": title,
                            "time": datetime.now(timezone.utc).isoformat(),
                        })
                        new_count += 1
                        if self.notifier:
                            await self.notifier.notify_announcement(title)
                        if len(self.recent) > 50:
                            self.recent = self.recent[-50:]
            except Exception as e:
                log.debug("公告扫描异常 (catalogId=%d): %s", cid, e)
        self._save_seen_ids()
        if new_count:
            log.info("公告扫描: 发现 %d 条新公告", new_count)


# ═══════════════════════════════════════════════
#  主程序
# ═══════════════════════════════════════════════

@dataclass
class AppState:
    strategy: str = "binance_earn"
    mode: str = "dry-run"
    started_at: str = ""
    uptime_s: int = 0
    earn_position: dict = field(default_factory=dict)
    locked_positions: list = field(default_factory=list)
    spot_bnb: float = 0.0
    airdrop_count: int = 0
    sell_count: int = 0
    recent_airdrops: list = field(default_factory=list)
    recent_sells: list = field(default_factory=list)
    announcements: list = field(default_factory=list)
    last_earn_check: str = ""
    last_airdrop_check: str = ""
    last_announce_check: str = ""
    errors: list = field(default_factory=list)


async def main(dry_run: bool):
    log.info("═" * 50)
    log.info("  BinanceEarn 启动 — %s 模式", "DRY-RUN" if dry_run else "LIVE")
    log.info("═" * 50)

    if not API_KEY or not API_SECRET or API_SECRET == "YOUR_SECRET_HERE":
        log.error("请在 .env 中配置 BINANCE_API_KEY 和 BINANCE_API_SECRET")
        sys.exit(1)

    state = AppState(
        mode="dry-run" if dry_run else "live",
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    start_time = time.time()

    async with aiohttp.ClientSession() as session:
        client = BinanceClient(API_KEY, API_SECRET, session)
        notifier = Notifier(session)
        earn = EarnManager(client, dry_run, notifier)
        tracker = AirdropTracker(client, dry_run, notifier)
        announcer = AnnouncementWatch(session, notifier)

        acct = await client.get_account()
        if acct.get("_error"):
            log.error("API 连接失败: %s — 请检查 API Key/Secret", acct.get("msg"))
            sys.exit(1)
        log.info("✅ API 连接成功")

        for b in acct.get("balances", []):
            total = float(b["free"]) + float(b["locked"])
            if total > 0 and b["asset"] in ("BNB", "USDC", "USDT"):
                log.info("  %s: %.8f (free=%.8f, locked=%.8f)",
                         b["asset"], total, float(b["free"]), float(b["locked"]))

        webhook_status = "已配置" if WEBHOOK_URL else "未配置"
        log.info("通知推送: %s | Locked Earn: %s (APR阈值=%.1f%%)",
                 webhook_status, "启用" if PREFER_LOCKED else "禁用",
                 LOCKED_APR_THRESHOLD * 100)

        last_earn = 0.0
        last_airdrop = 0.0
        last_announce = 0.0
        last_state = 0.0

        log.info("进入主循环 (earn=%ds, airdrop=%ds, announce=%ds, state=%ds)",
                 EARN_CHECK_INTERVAL, AIRDROP_CHECK_INTERVAL,
                 ANNOUNCE_CHECK_INTERVAL, STATE_WRITE_INTERVAL)

        while True:
            now = time.time()
            try:
                if now - last_earn >= EARN_CHECK_INTERVAL:
                    last_earn = now
                    state.last_earn_check = datetime.now(timezone.utc).isoformat()
                    err = await earn.ensure_subscribed()
                    if err:
                        log.warning("锁仓检查: %s", err)
                        state.errors.append({"time": state.last_earn_check, "msg": err})
                    await earn.refresh_position()
                    state.earn_position = earn.earn_position
                    state.locked_positions = earn.locked_positions

                    acct2 = await client.get_account()
                    if not acct2.get("_error"):
                        for b2 in acct2.get("balances", []):
                            if b2["asset"] == "BNB":
                                state.spot_bnb = float(b2["free"])

                if now - last_airdrop >= AIRDROP_CHECK_INTERVAL:
                    last_airdrop = now
                    state.last_airdrop_check = datetime.now(timezone.utc).isoformat()
                    new_drops = await tracker.scan_and_sell()
                    state.airdrop_count += len(new_drops)
                    state.sell_count = len(tracker.sell_history)
                    state.recent_airdrops = (state.recent_airdrops + new_drops)[-20:]
                    state.recent_sells = tracker.sell_history[-20:]

                if now - last_announce >= ANNOUNCE_CHECK_INTERVAL:
                    last_announce = now
                    state.last_announce_check = datetime.now(timezone.utc).isoformat()
                    await announcer.check()
                    state.announcements = announcer.recent[-10:]

                if now - last_state >= STATE_WRITE_INTERVAL:
                    last_state = now
                    state.uptime_s = int(now - start_time)
                    state.errors = state.errors[-10:]
                    STATE_FILE.write_text(json.dumps(asdict(state), indent=2, default=str))

            except Exception as e:
                log.exception("主循环异常: %s", e)
                err_msg = str(e)
                state.errors.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "msg": err_msg,
                })
                if notifier:
                    await notifier.notify_error(err_msg)

            await asyncio.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BinanceEarn 全自动理财系统")
    parser.add_argument("--dry-run", action="store_true", help="模拟模式（只查询不操作）")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.dry_run))
    except KeyboardInterrupt:
        log.info("用户中断，退出")
