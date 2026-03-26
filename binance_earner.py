#!/usr/bin/env python3
"""BinanceEarn — 全自动 BNB 理财 & 空投系统（模式 A：完全无人值守）"""

import argparse
import asyncio
import csv
import hashlib
import hmac
import json
import logging
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "earner.log")]
        if not sys.stdout.isatty()
        else [logging.StreamHandler(), logging.FileHandler(LOG_DIR / "earner.log")],
)
log = logging.getLogger("binance_earner")

API_KEY = os.getenv("BINANCE_API_KEY", "")
API_SECRET = os.getenv("BINANCE_API_SECRET", "")

MIN_SUBSCRIBE_BNB = float(os.getenv("BN_MIN_SUBSCRIBE_BNB", "0.01"))
RESERVE_BNB = float(os.getenv("BN_RESERVE_BNB", "0.005"))
AUTO_SELL = os.getenv("BN_AUTO_SELL_AIRDROP", "true").lower() == "true"
AUTO_COMPOUND = os.getenv("BN_AUTO_COMPOUND", "false").lower() == "true"

EARN_CHECK_INTERVAL = int(os.getenv("BN_EARN_CHECK_INTERVAL", "300"))
AIRDROP_CHECK_INTERVAL = int(os.getenv("BN_AIRDROP_CHECK_INTERVAL", "1800"))
ANNOUNCE_CHECK_INTERVAL = int(os.getenv("BN_ANNOUNCE_CHECK_INTERVAL", "1800"))
STATE_WRITE_INTERVAL = int(os.getenv("BN_STATE_WRITE_INTERVAL", "30"))

BASE_URL = "https://api.binance.com"
SKIP_ASSETS = {"BNB", "USDC", "USDT", "BUSD", "FDUSD", "USD", "EUR",
               "LDBNB", "WBNB", "BFUSD"}


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

    async def subscribe_earn_flexible(self, product_id: str, amount: float) -> dict:
        return await self._request("POST", "/sapi/v1/simple-earn/flexible/subscribe",
                                   {"productId": product_id, "amount": f"{amount:.8f}"})

    async def get_earn_position(self, asset: str = "") -> dict:
        params = {"size": 100}
        if asset:
            params["asset"] = asset
        return await self._request("GET", "/sapi/v1/simple-earn/flexible/position", params)

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
#  EarnManager — BNB 自动锁仓
# ═══════════════════════════════════════════════

class EarnManager:
    def __init__(self, client: BinanceClient, dry_run: bool):
        self.client = client
        self.dry_run = dry_run
        self.earn_position: dict = {}
        self.last_subscribe_time: float = 0

    async def ensure_subscribed(self) -> str | None:
        """检查现货 BNB，多余部分自动锁入 Simple Earn Flexible"""
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
            log.info("现货 BNB=%.6f，可锁仓=%.6f < 阈值 %.4f，跳过",
                     bnb_free, max(0, available), MIN_SUBSCRIBE_BNB)
            return None

        products = await self.client.get_earn_flexible_list("BNB")
        if products.get("_error"):
            return f"产品列表查询失败: {products.get('msg')}"

        rows = products.get("rows", [])
        if not rows:
            return "未找到 BNB Flexible Earn 产品"

        product = rows[0]
        product_id = product.get("productId")
        min_amount = float(product.get("minPurchaseAmount", "0.001"))
        amount = max(min_amount, available)

        log.info("准备锁仓 %.6f BNB → 产品 %s (最小 %.6f)", amount, product_id, min_amount)

        if self.dry_run:
            log.info("[DRY-RUN] 跳过实际锁仓操作")
            return None

        result = await self.client.subscribe_earn_flexible(product_id, amount)
        if result.get("_error"):
            return f"锁仓失败: {result.get('msg')}"

        self.last_subscribe_time = time.time()
        log.info("✅ 成功锁仓 %.6f BNB, purchaseId=%s", amount, result.get("purchaseId"))
        return None

    async def refresh_position(self):
        """刷新 Earn 持仓信息"""
        pos = await self.client.get_earn_position("BNB")
        if pos.get("_error"):
            log.warning("Earn 持仓查询失败: %s", pos.get("msg"))
            return
        rows = pos.get("rows", [])
        if rows:
            r = rows[0]
            self.earn_position = {
                "asset": r.get("asset", "BNB"),
                "totalAmount": r.get("totalAmount", "0"),
                "latestAnnualPercentageRate": r.get("latestAnnualPercentageRate", "0"),
                "totalRewards": r.get("totalRewards", "0"),
                "yesterdayRealTimeRewards": r.get("yesterdayRealTimeRewards", "0"),
                "cumulativeBonusRewards": r.get("cumulativeBonusRewards", "0"),
                "cumulativeRealTimeRewards": r.get("cumulativeRealTimeRewards", "0"),
                "cumulativeTotalRewards": r.get("cumulativeTotalRewards", "0"),
            }
            log.info("Earn 持仓: %.6s BNB, APR=%.2f%%, 累计收益=%s",
                     self.earn_position["totalAmount"],
                     float(self.earn_position["latestAnnualPercentageRate"]) * 100,
                     self.earn_position["cumulativeTotalRewards"])
        else:
            self.earn_position = {}
            log.info("暂无 Earn 持仓")


# ═══════════════════════════════════════════════
#  AirdropTracker — 空投检测 & 自动卖出
# ═══════════════════════════════════════════════

class AirdropTracker:
    def __init__(self, client: BinanceClient, dry_run: bool):
        self.client = client
        self.dry_run = dry_run
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
        """按交易对精度格式化数量"""
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
            return

        for pinfo in sorted(pairs, key=lambda p: {"USDC": 0, "USDT": 1, "BTC": 2}.get(p["quote"], 9)):
            qty_str = self._format_quantity(amount, pinfo)
            if not qty_str:
                continue

            symbol = pinfo["symbol"]
            log.info("准备卖出 %s %s → %s", qty_str, asset, symbol)

            if self.dry_run:
                log.info("[DRY-RUN] 跳过实际卖出")
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
#  AnnouncementWatch — 公告监控（仅日志）
# ═══════════════════════════════════════════════

class AnnouncementWatch:
    ANNOUNCE_URL = "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
    KEYWORDS = re.compile(r"launchpool|hodler|airdrop|earn", re.IGNORECASE)

    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.seen_ids: set[int] = set()
        self.recent: list[dict] = []

    async def check(self):
        try:
            payload = {
                "type": 1,
                "catalogId": 48,
                "pageNo": 1,
                "pageSize": 20,
            }
            async with self.session.post(self.ANNOUNCE_URL, json=payload,
                                         timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    log.debug("公告 API 状态码 %d", resp.status)
                    return
                data = await resp.json()

            articles = data.get("data", {}).get("catalogs", [{}])
            for catalog in articles:
                for art in catalog.get("articles", []):
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
                        if len(self.recent) > 50:
                            self.recent = self.recent[-50:]
        except Exception as e:
            log.debug("公告扫描异常: %s", e)


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
        earn = EarnManager(client, dry_run)
        tracker = AirdropTracker(client, dry_run)
        announcer = AnnouncementWatch(session)

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

        last_earn = 0.0
        last_airdrop = 0.0
        last_announce = 0.0
        last_state = 0.0

        log.info("进入主循环 (earn=%ds, airdrop=%ds, announce=%ds)",
                 EARN_CHECK_INTERVAL, AIRDROP_CHECK_INTERVAL, ANNOUNCE_CHECK_INTERVAL)

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
                state.errors.append({
                    "time": datetime.now(timezone.utc).isoformat(),
                    "msg": str(e),
                })

            await asyncio.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BinanceEarn 全自动理财系统")
    parser.add_argument("--dry-run", action="store_true", help="模拟模式（只查询不操作）")
    args = parser.parse_args()
    try:
        asyncio.run(main(args.dry_run))
    except KeyboardInterrupt:
        log.info("用户中断，退出")
