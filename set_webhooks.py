import os
import json
import sys
import time
import argparse
import requests

def set_webhooks(alwaysdata_domain):
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, 'config.json')
    
    if not os.path.exists(config_path):
        print(f"Error: Config file not found at {config_path}", flush=True)
        sys.exit(1)
        
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
        
    stores = config.get('stores', [])
    enabled_stores = [s for s in stores if s.get('enabled', True)]
    
    print(f"Setting Telegram Webhooks for {len(enabled_stores)} bots on {alwaysdata_domain}...", flush=True)
    
    for store in enabled_stores:
        token = store.get('telegram_bot_token')
        raw_domain = store.get('domain', '')
        if not token or not raw_domain:
            continue
            
        clean_domain = raw_domain.replace('https://', '').replace('http://', '').replace('www.', '').replace('.', '_')
        webhook_url = f"https://{alwaysdata_domain}/webhook/{clean_domain}"
        
        api_url = f"https://api.telegram.org/bot{token}/setWebhook"
        r = requests.post(api_url, json={'url': webhook_url})
        res = r.json()
        if res.get('ok'):
            print(f"✅ [{store.get('name')}] Webhook set successfully to {webhook_url}", flush=True)
        else:
            print(f"❌ [{store.get('name')}] Webhook set failed: {res.get('description')}", flush=True)
        time.sleep(1.0)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Register AlwaysData Telegram Webhooks for all bots")
    parser.add_argument("--domain", required=True, help="Your AlwaysData domain (e.g. accountname.alwaysdata.net)")
    args = parser.parse_args()
    set_webhooks(args.domain)
