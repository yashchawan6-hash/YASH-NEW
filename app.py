import os
import json
from datetime import datetime, timezone, timedelta
from flask import Flask, request, jsonify
import requests

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DAILY_DIR = os.path.join(BASE_DIR, 'daily_sales')

def get_ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

def generate_daily_sales_report(clean_domain, store_name):
    ist_now = get_ist_now()
    date_str = ist_now.strftime('%Y-%m-%d')
    display_date = ist_now.strftime('%d %B %Y')
    time_str = ist_now.strftime('%I:%M %p IST')
    
    sales = []
    local_file = os.path.join(DAILY_DIR, f"{clean_domain}_{date_str}.json")
    
    if os.path.exists(local_file):
        try:
            with open(local_file, 'r', encoding='utf-8') as f:
                sales = json.load(f)
        except Exception:
            sales = []
    else:
        # Fallback to fetching live daily sales JSON from GitHub repository
        github_url = f"https://raw.githubusercontent.com/yashchawan6-hash/YASH-NEW/main/daily_sales/{clean_domain}_{date_str}.json"
        try:
            r = requests.get(github_url, timeout=5)
            if r.status_code == 200:
                sales = r.json()
        except Exception:
            sales = []

    if not sales:
        return (
            f"📊 <b>DAILY SALES REPORT - {store_name.upper()}</b>\n"
            f"📅 <b>Date:</b> {display_date} (12:00 AM IST ➔ Present)\n\n"
            f"ℹ️ No sales recorded yet for today.\n\n"
            f"⏰ <i>Generated live at {time_str}</i>"
        )
        
    total_qty = sum(item.get('qty_sold', 0) for item in sales)
    total_revenue = sum(item.get('total_value', 0.0) for item in sales)
    currency_symbol = '₹' if sales[0].get('currency') == 'INR' else '$'
    
    grouped = {}
    for item in sales:
        key = (item.get('product_title', ''), item.get('variant_title', ''), item.get('price', 0.0))
        if key not in grouped:
            grouped[key] = {'qty': 0, 'revenue': 0.0}
        grouped[key]['qty'] += item.get('qty_sold', 0)
        grouped[key]['revenue'] += item.get('total_value', 0.0)
        
    items_lines = []
    for (p_title, v_title, price), stats in grouped.items():
        v_str = f" ({v_title})" if v_title and v_title.lower() != 'default title' else ""
        items_lines.append(f"• <b>{p_title}{v_str}</b> — <code>{stats['qty']} sold</code> ({currency_symbol}{stats['revenue']:,.2f})")
        
    items_text = "\n".join(items_lines[:25])
    if len(items_lines) > 25:
        items_text += f"\n<i>...and {len(items_lines) - 25} more items.</i>"
        
    report = (
        f"📊 <b>DAILY SALES REPORT - {store_name.upper()}</b>\n"
        f"📅 <b>Date:</b> {display_date} (12:00 AM IST ➔ Present)\n\n"
        f"🔥 <b>Total Units Sold Today:</b> <code>{total_qty} units</code>\n"
        f"💰 <b>Total Sales Revenue:</b> <code>{currency_symbol}{total_revenue:,.2f}</code>\n\n"
        f"🛍️ <b>Items Sold Today:</b>\n"
        f"{items_text}\n\n"
        f"⏰ <i>Generated live at {time_str}</i>"
    )
    return report

@app.route('/')
def home():
    return "Shopify Telegram Sales Tracker Webhook Server Active!", 200

@app.route('/webhook/<store_key>', methods=['POST'])
def handle_webhook(store_key):
    data = request.get_json(force=True, silent=True) or {}
    msg = data.get('message', {}) or data.get('channel_post', {})
    text = (msg.get('text') or '').strip().lower()
    
    if '/sale' in text or '/sales' in text:
        config_path = os.path.join(BASE_DIR, 'config.json')
        if os.path.exists(config_path):
            with open(config_path, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
            stores = cfg.get('stores', [])
            target_store = next((s for s in stores if s.get('domain', '').replace('.', '_') == store_key), None)
            if target_store:
                bot_token = target_store.get('telegram_bot_token')
                chat_id = msg.get('chat', {}).get('id') or target_store.get('telegram_chat_id')
                store_name = target_store.get('name', 'Store')
                
                report = generate_daily_sales_report(store_key, store_name)
                requests.post(f"https://api.telegram.org/bot{bot_token}/sendMessage", json={
                    'chat_id': chat_id,
                    'text': report,
                    'parse_mode': 'HTML'
                }, timeout=10)
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
