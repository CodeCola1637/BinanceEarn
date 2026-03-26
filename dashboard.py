#!/usr/bin/env python3
"""BinanceEarn Dashboard — Web 看板"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify

load_dotenv()

app = Flask(__name__)
STATE_FILE = Path("logs/state.json")
EARNINGS_CSV = Path("logs/earnings.csv")
PORT = int(os.getenv("DASHBOARD_PORT", "8080"))

HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>BinanceEarn Dashboard</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: -apple-system, 'Segoe UI', Roboto, sans-serif;
    background: #0d1117; color: #e6edf3;
    padding: 20px; max-width: 1100px; margin: 0 auto;
  }
  h1 { color: #f0b90b; margin-bottom: 8px; font-size: 1.6em; }
  .subtitle { color: #8b949e; margin-bottom: 24px; font-size: 0.9em; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .card {
    background: #161b22; border: 1px solid #30363d; border-radius: 10px;
    padding: 18px; transition: border-color 0.2s;
  }
  .card:hover { border-color: #f0b90b; }
  .card-label { color: #8b949e; font-size: 0.8em; text-transform: uppercase; letter-spacing: 0.5px; }
  .card-value { font-size: 1.5em; font-weight: 700; margin-top: 6px; }
  .card-sub { color: #8b949e; font-size: 0.8em; margin-top: 4px; }
  .gold { color: #f0b90b; }
  .green { color: #3fb950; }
  .red { color: #f85149; }
  .blue { color: #58a6ff; }
  .section { margin-bottom: 24px; }
  .section h2 { color: #c9d1d9; font-size: 1.1em; margin-bottom: 12px; border-bottom: 1px solid #30363d; padding-bottom: 6px; }
  table { width: 100%; border-collapse: collapse; font-size: 0.85em; }
  th { text-align: left; color: #8b949e; padding: 8px 12px; border-bottom: 1px solid #30363d; font-weight: 500; }
  td { padding: 8px 12px; border-bottom: 1px solid #21262d; }
  tr:hover td { background: #1c2128; }
  .badge {
    display: inline-block; padding: 2px 8px; border-radius: 12px;
    font-size: 0.75em; font-weight: 600;
  }
  .badge-live { background: #1a4731; color: #3fb950; }
  .badge-dry { background: #3d2e00; color: #f0b90b; }
  .empty { color: #484f58; font-style: italic; padding: 16px 0; }
  .footer { color: #484f58; font-size: 0.75em; text-align: center; margin-top: 32px; }
  @media (max-width: 600px) {
    .grid { grid-template-columns: 1fr; }
    .card-value { font-size: 1.2em; }
  }
</style>
</head>
<body>
  <h1>BinanceEarn Dashboard</h1>
  <div class="subtitle" id="status">加载中...</div>

  <div class="grid" id="cards"></div>

  <div class="section" id="earn-section">
    <h2>Simple Earn 持仓</h2>
    <div id="earn-info" class="empty">暂无数据</div>
  </div>

  <div class="section">
    <h2>空投记录</h2>
    <div id="airdrop-list" class="empty">暂无空投</div>
  </div>

  <div class="section">
    <h2>卖出记录</h2>
    <div id="sell-list" class="empty">暂无卖出</div>
  </div>

  <div class="section">
    <h2>公告监控</h2>
    <div id="announce-list" class="empty">暂无新公告</div>
  </div>

  <div class="section" id="error-section" style="display:none">
    <h2 class="red">错误日志</h2>
    <div id="error-list"></div>
  </div>

  <div class="footer">自动刷新间隔 10 秒 · BinanceEarn v1.0</div>

<script>
function fmt(v, d=4) { return v ? parseFloat(v).toFixed(d) : '0'; }
function fmtTime(t) {
  if (!t) return '-';
  try { return new Date(t).toLocaleString('zh-CN', {hour12:false}); } catch { return t; }
}
function uptimeStr(s) {
  if (!s) return '-';
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60);
  return h > 0 ? h+'h '+m+'m' : m+'m';
}

async function refresh() {
  try {
    const r = await fetch('/api/state');
    const d = await r.json();

    const mode = d.mode || 'unknown';
    const badge = mode === 'live'
      ? '<span class="badge badge-live">LIVE</span>'
      : '<span class="badge badge-dry">DRY-RUN</span>';
    document.getElementById('status').innerHTML =
      badge + ' 运行 ' + uptimeStr(d.uptime_s) + ' · 上次更新 ' + fmtTime(d.last_earn_check);

    const ep = d.earn_position || {};
    const cards = [
      {label:'Earn BNB', value: fmt(ep.totalAmount, 6), cls:'gold', sub:'锁仓数量'},
      {label:'年化利率', value: fmt(parseFloat(ep.latestAnnualPercentageRate||0)*100, 2)+'%', cls:'green', sub:'APR'},
      {label:'累计收益', value: fmt(ep.cumulativeTotalRewards, 8)+' BNB', cls:'green', sub:'利息+奖励'},
      {label:'现货 BNB', value: fmt(d.spot_bnb, 6), cls:'blue', sub:'可用余额'},
      {label:'空投检测', value: d.airdrop_count || 0, cls:'gold', sub:'累计发现'},
      {label:'自动卖出', value: d.sell_count || 0, cls:'green', sub:'累计笔数'},
    ];
    document.getElementById('cards').innerHTML = cards.map(c =>
      '<div class="card"><div class="card-label">'+c.label+'</div>' +
      '<div class="card-value '+c.cls+'">'+c.value+'</div>' +
      '<div class="card-sub">'+c.sub+'</div></div>'
    ).join('');

    if (ep.totalAmount && parseFloat(ep.totalAmount) > 0) {
      document.getElementById('earn-info').innerHTML =
        '<table><tr><th>锁仓量</th><th>APR</th><th>昨日收益</th><th>累计收益</th></tr>' +
        '<tr><td class="gold">'+fmt(ep.totalAmount,6)+' BNB</td>' +
        '<td class="green">'+fmt(parseFloat(ep.latestAnnualPercentageRate||0)*100,2)+'%</td>' +
        '<td>'+fmt(ep.yesterdayRealTimeRewards,8)+' BNB</td>' +
        '<td class="green">'+fmt(ep.cumulativeTotalRewards,8)+' BNB</td></tr></table>';
    }

    const airdrops = d.recent_airdrops || [];
    if (airdrops.length) {
      let h = '<table><tr><th>时间</th><th>代币</th><th>数量</th><th>类型</th></tr>';
      airdrops.slice().reverse().forEach(a => {
        h += '<tr><td>'+fmtTime(a.time)+'</td><td class="gold">'+a.asset+'</td>' +
             '<td>'+fmt(a.amount,8)+'</td><td>'+(a.is_new?'新币':'增加')+'</td></tr>';
      });
      document.getElementById('airdrop-list').innerHTML = h + '</table>';
    }

    const sells = d.recent_sells || [];
    if (sells.length) {
      let h = '<table><tr><th>时间</th><th>代币</th><th>数量</th><th>获得</th></tr>';
      sells.slice().reverse().forEach(s => {
        h += '<tr><td>'+fmtTime(s.time)+'</td><td>'+s.asset+'</td>' +
             '<td>'+s.quantity+'</td><td class="green">'+s.quote_amount+' '+s.quote_asset+'</td></tr>';
      });
      document.getElementById('sell-list').innerHTML = h + '</table>';
    }

    const anns = d.announcements || [];
    if (anns.length) {
      let h = '<table><tr><th>时间</th><th>公告标题</th></tr>';
      anns.slice().reverse().forEach(a => {
        h += '<tr><td>'+fmtTime(a.time)+'</td><td>'+a.title+'</td></tr>';
      });
      document.getElementById('announce-list').innerHTML = h + '</table>';
    }

    const errs = d.errors || [];
    if (errs.length) {
      document.getElementById('error-section').style.display = '';
      let h = '<table><tr><th>时间</th><th>错误</th></tr>';
      errs.slice().reverse().forEach(e => {
        h += '<tr><td>'+fmtTime(e.time)+'</td><td class="red">'+e.msg+'</td></tr>';
      });
      document.getElementById('error-list').innerHTML = h + '</table>';
    } else {
      document.getElementById('error-section').style.display = 'none';
    }
  } catch(e) {
    document.getElementById('status').innerHTML = '<span class="red">数据加载失败</span>';
  }
}

refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>"""


@app.route("/")
def index():
    return HTML


@app.route("/api/state")
def api_state():
    if STATE_FILE.exists():
        try:
            return jsonify(json.loads(STATE_FILE.read_text()))
        except Exception:
            pass
    return jsonify({})


if __name__ == "__main__":
    print(f"Dashboard 启动: http://0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False)
