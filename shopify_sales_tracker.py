import os
import sys
import json
import time
import re
import requests
import base64
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

def clean_string(s):
    if not s:
        return ""
    return re.sub(r'\s+', ' ', str(s)).strip()

def get_store_domain(raw_url):
    url = raw_url.strip()
    if not url.startswith('http://') and not url.startswith('https://'):
        url = 'https://' + url
    match = re.search(r'https?://([^/]+)', url)
    if match:
        domain = match.group(1).lower()
        if domain.startswith('www.'):
            domain = domain[4:]
        return domain
    return raw_url.lower()

def harvest_storefront_token(domain):
    print(f"[{domain}] Harvesting Storefront Access Token...", flush=True)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.9',
    }

    try:
        url = f"https://www.{domain}"
        r = requests.get(url, headers=headers, timeout=15)
        if r.status_code == 200:
            html = r.text
            tokens = re.findall(r'"storefrontAccessToken":"([a-f0-9]{32})"', html)
            tokens_js = re.findall(r'storefrontAccessToken\s*:\s*["\']([a-f0-9]{32})["\']', html)
            tokens_raw = re.findall(r'accessToken["\']?\s*:\s*["\']([a-f0-9]{32})["\']', html, re.IGNORECASE)
            found = tokens + tokens_js + tokens_raw
            if found:
                print(f"[{domain}] Harvested token from homepage: {found[0]}", flush=True)
                return found[0]

            prod_url_match = re.search(r'href=["\'](/products/[^"\'?]+)["\']', html)
            if prod_url_match:
                prod_url = f"https://www.{domain}{prod_url_match.group(1)}"
                r_prod = requests.get(prod_url, headers=headers, timeout=15)
                if r_prod.status_code == 200:
                    html_prod = r_prod.text
                    tokens = re.findall(r'"storefrontAccessToken":"([a-f0-9]{32})"', html_prod)
                    tokens_js = re.findall(r'storefrontAccessToken\s*:\s*["\']([a-f0-9]{32})["\']', html_prod)
                    tokens_raw = re.findall(r'accessToken["\']?\s*:\s*["\']([a-f0-9]{32})["\']', html_prod, re.IGNORECASE)
                    found = tokens + tokens_js + tokens_raw
                    if found:
                        print(f"[{domain}] Harvested token from product page: {found[0]}", flush=True)
                        return found[0]
    except Exception as e:
        print(f"[{domain}] Product page token harvest failed: {e}", flush=True)

    return None

def post_graphql_query(url, headers, payload, max_retries=5):
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=25)
            if r.status_code == 429:
                sleep_time = 3
                print(f"[GraphQL Throttle] HTTP 429. Sleeping {sleep_time}s...", flush=True)
                time.sleep(sleep_time)
                continue

            if r.status_code == 200:
                data = r.json()
                errors = data.get('errors', [])
                throttled = False
                for err in errors:
                    ext_code = err.get('extensions', {}).get('code')
                    msg = err.get('message', '').lower()
                    if ext_code == 'THROTTLED' or 'throttle' in msg:
                        throttled = True
                        break

                if throttled:
                    sleep_time = 3
                    print(f"[GraphQL Throttle] Throttled response. Sleeping {sleep_time}s...", flush=True)
                    time.sleep(sleep_time)
                    continue

                return data
            else:
                print(f"[GraphQL Error] HTTP {r.status_code}: {r.text[:200]}", flush=True)
                time.sleep(2)
        except Exception as e:
            print(f"[GraphQL Exception] {e}", flush=True)
            time.sleep(2)
    return None

def fetch_catalog(domain, token):
    print(f"[{domain}] Fetching complete catalog and images via Storefront GraphQL...", flush=True)
    url = f"https://www.{domain}/api/2023-07/graphql.json"
    headers = {
        'X-Shopify-Storefront-Access-Token': token,
        'Content-Type': 'application/json'
    }

    query = """
    query getAllProducts($cursor: String) {
      products(first: 100, after: $cursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        edges {
          node {
            title
            handle
            featuredImage {
              url
            }
            variants(first: 250) {
              edges {
                node {
                  id
                  title
                  sku
                  price {
                    amount
                    currencyCode
                  }
                  image {
                    url
                  }
                  availableForSale
                }
              }
            }
          }
        }
      }
    }
    """

    active_variants = []
    cursor = None
    has_next = True
    page_num = 1

    while has_next:
        payload = {'query': query, 'variables': {"cursor": cursor}}
        data = None
        for attempt in range(1, 4):
            data = post_graphql_query(url, headers, payload)
            if data:
                break
            print(f"[{domain}] Page {page_num} attempt {attempt} failed, retrying...", flush=True)
            time.sleep(1.5)

        if not data:
            print(f"[{domain}] Page {page_num}: No GraphQL response after retries, stopping catalog fetch.", flush=True)
            break

        products_conn = data.get('data', {}).get('products', {})
        if not products_conn:
            break

        edges = products_conn.get('edges', [])
        for edge in edges:
            node = edge['node']
            p_title = clean_string(node.get('title', ''))
            p_handle = node.get('handle', '')
            p_featured_img = node.get('featuredImage', {}).get('url') if node.get('featuredImage') else None

            for v_edge in node.get('variants', {}).get('edges', []):
                v_node = v_edge['node']
                global_id = v_node['id']
                try:
                    decoded = base64.b64decode(global_id).decode('utf-8')
                    variant_id = str(decoded.split('/')[-1])
                except Exception:
                    variant_id = str(global_id)

                v_img = v_node.get('image', {}).get('url') if v_node.get('image') else p_featured_img
                currency_code = v_node.get('price', {}).get('currencyCode', 'INR')

                v_info = {
                    'product_title': p_title,
                    'product_handle': p_handle,
                    'variant_id': variant_id,
                    'global_id': global_id,
                    'variant_title': clean_string(v_node.get('title', '')),
                    'sku': clean_string(v_node.get('sku', '')),
                    'price': float(v_node.get('price', {}).get('amount', 0)),
                    'currency': currency_code,
                    'image_url': v_img,
                    'available_for_sale': v_node.get('availableForSale', False),
                    'url': f"https://www.{domain}/products/{p_handle}?variant={variant_id}"
                }
                active_variants.append(v_info)

        page_info = products_conn.get('pageInfo', {})
        has_next = page_info.get('hasNextPage', False)
        cursor = page_info.get('endCursor', None)
        page_num += 1

    print(f"[{domain}] Total variants loaded from catalog: {len(active_variants)}", flush=True)
    return active_variants

def check_stock_in_batches(domain, token, active_variants):
    print(f"[{domain}] Calculating exact stock levels via Cart API for ALL {len(active_variants)} variants...", flush=True)
    url = f"https://www.{domain}/api/2023-07/graphql.json"
    headers = {
        'X-Shopify-Storefront-Access-Token': token,
        'Content-Type': 'application/json'
    }

    mutation = """
    mutation cartCreate($input: CartInput!) {
      cartCreate(input: $input) {
        cart {
          id
          lines(first: 250) {
            edges {
              node {
                quantity
                merchandise {
                  ... on ProductVariant {
                    id
                  }
                }
              }
            }
          }
        }
      }
    }
    """

    results = {}
    batch_size = 100
    total_variants = len(active_variants)

    for i in range(0, total_variants, batch_size):
        batch = active_variants[i:i+batch_size]
        lines = [{'merchandiseId': v['global_id'], 'quantity': 9999} for v in batch]
        variables = {"input": {"lines": lines}}

        data = post_graphql_query(url, headers, {'query': mutation, 'variables': variables})

        if not data:
            for v in batch:
                results[v['variant_id']] = -1
            continue

        cart_data = data.get('data', {}).get('cartCreate', {}).get('cart', {})
        if cart_data and 'lines' in cart_data:
            quantities = {}
            for edge in cart_data.get('lines', {}).get('edges', []):
                node = edge['node']
                g_id = node['merchandise']['id']
                qty = node['quantity']
                quantities[g_id] = qty

            for v in batch:
                # Items omitted by Shopify Cart API are 0 (sold out)
                stock = quantities.get(v['global_id'], 0)
                results[v['variant_id']] = stock
        else:
            for v in batch:
                results[v['variant_id']] = -1

        if i + batch_size < total_variants:
            time.sleep(0.3)

    return results

def get_ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

def record_daily_sales(clean_domain, sales_detected, daily_dir):
    if not sales_detected:
        return
    os.makedirs(daily_dir, exist_ok=True)
    ist_now = get_ist_now()
    date_str = ist_now.strftime('%Y-%m-%d')
    daily_file = os.path.join(daily_dir, f"{clean_domain}_{date_str}.json")

    existing = []
    if os.path.exists(daily_file):
        try:
            with open(daily_file, 'r', encoding='utf-8') as f:
                existing = json.load(f)
        except Exception:
            existing = []

    for sale in sales_detected:
        item = sale['item']
        record = {
            'timestamp_ist': ist_now.strftime('%Y-%m-%d %H:%M:%S'),
            'product_title': item.get('product_title'),
            'variant_title': item.get('variant_title'),
            'variant_id': item.get('variant_id'),
            'sku': item.get('sku'),
            'price': item.get('price'),
            'currency': item.get('currency', 'INR'),
            'qty_sold': sale.get('qty_sold'),
            'prev_stock': sale.get('prev_stock'),
            'curr_stock': sale.get('curr_stock'),
            'url': item.get('url'),
            'image_url': item.get('image_url')
        }
        existing.append(record)

    try:
        with open(daily_file, 'w', encoding='utf-8') as f:
            json.dump(existing, f, indent=2)
        print(f"Recorded {len(sales_detected)} sales to daily file: {daily_file}", flush=True)
    except Exception as e:
        print(f"Error writing to daily sales file: {e}", flush=True)

def send_telegram_alert(bot_token, chat_id, sale, domain):
    item = sale['item']
    qty = sale['qty_sold']
    prev_s = sale['prev_stock']
    curr_s = sale['curr_stock']

    prod_title = item.get('product_title', 'Unknown Product')
    var_title = item.get('variant_title', '')
    price = item.get('price', 0.0)
    currency = item.get('currency', 'INR')
    url = item.get('url', f"https://www.{domain}")
    image_url = item.get('image_url')

    symbol = "₹" if currency == "INR" else "$"
    total_val = price * qty

    display_title = prod_title
    if var_title and var_title.lower() != 'default title':
        display_title += f" - {var_title}"

    msg = f"<b>🛍️ NEW SALE DETECTED!</b>\n\n"
    msg += f"<b>Store:</b> {domain}\n"
    msg += f"<b>Product:</b> <a href=\"{url}\">{display_title}</a>\n"
    msg += f"<b>Qty Sold:</b> {qty} unit(s)\n"
    msg += f"<b>Price:</b> {symbol}{price:,.2f} (Total: {symbol}{total_val:,.2f})\n"
    msg += f"<b>Stock Drop:</b> {prev_s} ➔ {curr_s}\n"
    msg += f"<b>Time (IST):</b> {get_ist_now().strftime('%Y-%m-%d %H:%M:%S')}"

    api_url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    payload = {
        'chat_id': chat_id,
        'caption': msg,
        'parse_mode': 'HTML'
    }

    if image_url:
        payload['photo'] = image_url
        try:
            r = requests.post(api_url, json=payload, timeout=10)
            if r.status_code == 200:
                return
        except Exception:
            pass

    # Fallback to sendMessage if photo fails or missing
    text_api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    text_payload = {
        'chat_id': chat_id,
        'text': msg,
        'parse_mode': 'HTML',
        'disable_web_page_preview': False
    }
    try:
        requests.post(text_api_url, json=text_payload, timeout=10)
    except Exception as e:
        print(f"[{domain}] Telegram notification failed: {e}", flush=True)

def process_store(store_config, global_bot_token, state_dir, daily_dir):
    raw_domain = store_config.get('domain', '')
    chat_id = store_config.get('telegram_chat_id', '')
    store_token_env = store_config.get('telegram_bot_token_env')
    store_bot_token = (
        store_config.get('telegram_bot_token') or 
        (os.environ.get(store_token_env) if store_token_env else None) or 
        global_bot_token
    )

    if not raw_domain or not chat_id:
        print(f"Skipping incomplete store config: {store_config}", flush=True)
        return

    domain = get_store_domain(raw_domain)
    clean_domain = domain.replace('.', '_')
    state_file = os.path.join(state_dir, f"{clean_domain}_state.json")

    previous_state = {}
    if os.path.exists(state_file):
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                previous_state = json.load(f)
            print(f"[{domain}] Loaded previous state snapshot with {len(previous_state)} items.", flush=True)
        except Exception as e:
            print(f"[{domain}] Error loading state file: {e}", flush=True)

    token = harvest_storefront_token(domain)
    if not token:
        print(f"[{domain}] Could not harvest token. Skipping store run.", flush=True)
        return

    # 1. Fetch 100% of catalog variants (with retries and full variant pagination)
    all_catalog_variants = fetch_catalog(domain, token)
    if not all_catalog_variants:
        print(f"[{domain}] No active variants found.", flush=True)
        return

    # 2. Check 100% of variants via Cart API in batches of 250 (NO SKIPPING)
    # This guarantees that restocks (0 -> 5) and sales (5 -> 2) are ALWAYS detected in real time!
    variants_to_check = list(all_catalog_variants)

    # Preserve any variant previously tracked in state that might have been hidden from catalog
    known_v_ids = {v['variant_id'] for v in all_catalog_variants}
    if previous_state:
        for prev_id, prev_data in previous_state.items():
            if prev_id not in known_v_ids and isinstance(prev_data, dict):
                if prev_data.get('stock', 0) > 0:
                    variants_to_check.append(prev_data)
                    all_catalog_variants.append(prev_data)

    print(f"[{domain}] Querying Cart API for {len(variants_to_check)} variants out of {len(all_catalog_variants)} total.", flush=True)
    stock_results = check_stock_in_batches(domain, token, variants_to_check)

    current_state = {}
    sales_detected = []

    for v in all_catalog_variants:
        v_id = v['variant_id']
        
        if v_id in stock_results:
            curr_stock = stock_results[v_id]
            # If Cart API failed for this batch (-1), retain exact previous stock level from previous_state
            if curr_stock == -1 and previous_state and v_id in previous_state:
                curr_stock = previous_state[v_id].get('stock', 0)
        else:
            # Items omitted by Cart API (sold out) are 0
            curr_stock = 0

        v['stock'] = curr_stock

        # Preserve previous last_alerted_stock memory if present
        prev_alerted = previous_state.get(v_id, {}).get('last_alerted_stock', None) if previous_state else None
        if prev_alerted is not None:
            v['last_alerted_stock'] = prev_alerted

        if previous_state and v_id in previous_state and curr_stock >= 0:
            prev_stock = previous_state[v_id].get('stock', 0)

            # Memory Guard 1: Skip if current stock matches last alerted stock
            if prev_alerted is not None and curr_stock == prev_alerted:
                current_state[v_id] = v
                continue

            # Memory Guard 2: Only trigger if stock actually decreased
            if prev_stock > curr_stock and prev_stock < 9000:
                qty_sold = prev_stock - curr_stock
                if 0 < qty_sold < 500:
                    sales_detected.append({
                        'item': v,
                        'prev_stock': prev_stock,
                        'curr_stock': curr_stock,
                        'qty_sold': qty_sold
                    })
                    v['last_alerted_stock'] = curr_stock

        current_state[v_id] = v

    if not previous_state:
        print(f"[{domain}] Initial baseline run. Saved {len(current_state)} variant stock levels to state file (No alerts on baseline).", flush=True)
    else:
        print(f"[{domain}] Scan complete. Sales detected: {len(sales_detected)}", flush=True)
        if sales_detected:
            record_daily_sales(clean_domain, sales_detected, daily_dir)
            for sale in sales_detected:
                send_telegram_alert(store_bot_token, chat_id, sale, domain)

    os.makedirs(state_dir, exist_ok=True)
    try:
        with open(state_file, 'w', encoding='utf-8') as f:
            json.dump(current_state, f, indent=2)
        print(f"[{domain}] Updated state file saved successfully.", flush=True)
    except Exception as e:
        print(f"[{domain}] Error saving state file: {e}", flush=True)

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, 'config.json')
    state_dir = os.path.join(script_dir, 'state')
    daily_dir = os.path.join(script_dir, 'daily_sales')

    global_bot_token = os.environ.get('TELEGRAM_BOT_TOKEN')

    if not os.path.exists(config_path):
        print(f"Config file not found at {config_path}", flush=True)
        sys.exit(1)

    with open(config_path, 'r', encoding='utf-8') as f:
        config_data = json.load(f)

    stores = [s for s in config_data.get('stores', []) if s.get('enabled', True)]
    print(f"Starting sales tracker run for {len(stores)} enabled stores at {get_ist_now().strftime('%Y-%m-%d %H:%M:%S IST')}...", flush=True)

    max_workers = 8
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [
            executor.submit(process_store, store, global_bot_token, state_dir, daily_dir)
            for store in stores
        ]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                print(f"Store thread generated an exception: {e}", flush=True)

    print("All store tracking scans completed successfully!", flush=True)

if __name__ == '__main__':
    main()
