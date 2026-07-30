import os
import sys
import json
import time
import re
import base64
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
import requests

def clean_string(val):
    if not isinstance(val, str):
        return val
    return re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f]', '', val)

def get_store_domain(url_str):
    if not url_str.startswith('http://') and not url_str.startswith('https://'):
        url_str = 'https://' + url_str
    parsed = urlparse(url_str)
    domain = parsed.netloc
    if domain.startswith('www.'):
        domain = domain[4:]
    return domain

def harvest_storefront_token(domain):
    print(f"[{domain}] Harvesting Storefront Access Token...", flush=True)
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    
    # Try Home Page first
    try:
        r = requests.get(f"https://www.{domain}/", headers=headers, timeout=15)
        html = r.text
        tokens = re.findall(r'"storefrontAccessToken":"([a-f0-9]{32})"', html)
        tokens_js = re.findall(r'storefrontAccessToken\s*:\s*["\']([a-f0-9]{32})["\']', html)
        tokens_raw = re.findall(r'accessToken["\']?\s*:\s*["\']([a-f0-9]{32})["\']', html, re.IGNORECASE)
        found = tokens + tokens_js + tokens_raw
        if found:
            print(f"[{domain}] Harvested token from homepage: {found[0]}", flush=True)
            return found[0]
    except Exception as e:
        print(f"[{domain}] Homepage token harvest failed: {e}", flush=True)

    # Try product page by loading products.json
    try:
        r_json = requests.get(f"https://www.{domain}/products.json?limit=3", headers=headers, timeout=15)
        if r_json.status_code == 200:
            products = r_json.json().get('products', [])
            if products:
                handle = products[0]['handle']
                prod_url = f"https://www.{domain}/products/{handle}"
                r_prod = requests.get(prod_url, headers=headers, timeout=15)
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
    print(f"[{domain}] Fetching catalog and images via Storefront GraphQL...", flush=True)
    url = f"https://www.{domain}/api/2023-07/graphql.json"
    headers = {
        'X-Shopify-Storefront-Access-Token': token,
        'Content-Type': 'application/json'
    }
    
    query = """
    query getAllProducts($cursor: String) {
      products(first: 250, after: $cursor) {
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
            variants(first: 100) {
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
        data = post_graphql_query(url, headers, payload)
        if not data:
            print(f"[{domain}] Page {page_num}: No GraphQL response", flush=True)
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
                
                # Always track all variants (both in-stock and newly sold-out)
                active_variants.append(v_info)
                    
        page_info = products_conn.get('pageInfo', {})
        has_next = page_info.get('hasNextPage', False)
        cursor = page_info.get('endCursor', None)
        page_num += 1
        
    print(f"[{domain}] Total variants loaded from catalog: {len(active_variants)}", flush=True)
    return active_variants

def check_stock_in_batches(domain, token, active_variants):
    print(f"[{domain}] Calculating exact stock levels via Cart API...", flush=True)
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
    batch_size = 150
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
                # If cart returned successfully, items omitted by Shopify have 0 stock (sold out)
                stock = quantities.get(v['global_id'], 0)
                results[v['variant_id']] = stock
        else:
            # Only mark -1 if the entire GraphQL request/cart creation failed
            for v in batch:
                results[v['variant_id']] = -1
                
        if i + batch_size < total_variants:
            time.sleep(0.8)
            
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
            'timestamp': ist_now.strftime('%Y-%m-%dT%H:%M:%S+05:30'),
            'product_title': item.get('product_title', ''),
            'variant_title': item.get('variant_title', ''),
            'price': item.get('price', 0.0),
            'currency': item.get('currency', 'INR'),
            'qty_sold': sale.get('qty_sold', 1),
            'total_value': item.get('price', 0.0) * sale.get('qty_sold', 1),
            'url': item.get('url', '')
        }
        existing.append(record)
        
    with open(daily_file, 'w', encoding='utf-8') as f:
        json.dump(existing, f, indent=2)

def generate_daily_sales_report(clean_domain, store_name, daily_dir):
    ist_now = get_ist_now()
    date_str = ist_now.strftime('%Y-%m-%d')
    display_date = ist_now.strftime('%d %B %Y')
    time_str = ist_now.strftime('%I:%M %p IST')
    
    daily_file = os.path.join(daily_dir, f"{clean_domain}_{date_str}.json")
    
    sales = []
    if os.path.exists(daily_file):
        try:
            with open(daily_file, 'r', encoding='utf-8') as f:
                sales = json.load(f)
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
        key = (item['product_title'], item['variant_title'], item['price'])
        if key not in grouped:
            grouped[key] = {'qty': 0, 'revenue': 0.0}
        grouped[key]['qty'] += item['qty_sold']
        grouped[key]['revenue'] += item['total_value']
        
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


def send_telegram_alert(bot_token, chat_id, store_name, item, prev_stock, curr_stock, qty_sold):
    print(f"[{store_name}] Sending Telegram Alert for product: {item['product_title']} (Sold: {qty_sold})", flush=True)
    
    currency_symbol = '₹' if item.get('currency') == 'INR' else '$'
    v_title_str = f" ({item['variant_title']})" if item['variant_title'] and item['variant_title'].lower() != 'default title' else ""
    
    caption = (
        f"🛍️ <b>NEW SALE DETECTED - {store_name.upper()}</b>\n\n"
        f"📦 <b>Product:</b> {item['product_title']}{v_title_str}\n"
        f"💰 <b>Price:</b> {currency_symbol}{item['price']:,.2f}\n"
        f"🔥 <b>Qty Sold:</b> <code>{qty_sold}</code>\n"
        f"📊 <b>Stock Update:</b> {prev_stock} ➔ <b>{curr_stock}</b> remaining\n\n"
        f"🔗 <a href=\"{item['url']}\">View Product on Store</a>"
    )

    photo_url = item.get('image_url')
    
    if photo_url:
        send_photo_endpoint = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
        payload = {
            'chat_id': chat_id,
            'photo': photo_url,
            'caption': caption,
            'parse_mode': 'HTML'
        }
        try:
            r = requests.post(send_photo_endpoint, json=payload, timeout=15)
            res = r.json()
            if res.get('ok'):
                return True
            else:
                print(f"Telegram sendPhoto Error: {res.get('description')}. Falling back to text message.", flush=True)
        except Exception as e:
            print(f"Exception sending photo to Telegram: {e}", flush=True)
            
    # Fallback to plain text message if image is missing or failed
    send_msg_endpoint = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        'chat_id': chat_id,
        'text': caption,
        'parse_mode': 'HTML',
        'disable_web_page_preview': False
    }
    try:
        r = requests.post(send_msg_endpoint, json=payload, timeout=15)
        res = r.json()
        if not res.get('ok'):
            print(f"Telegram sendMessage Error: {res.get('description')}", flush=True)
            return False
        return True
    except Exception as e:
        print(f"Exception sending message to Telegram: {e}", flush=True)
        return False

def process_store(store_config, global_bot_token, state_dir, daily_dir):
    store_name = store_config.get('name', 'Shopify Store')
    raw_domain = store_config.get('domain', '')
    chat_id = store_config.get('telegram_chat_id', '')
    
    # Allow per-store bot token or fallback to global bot token
    store_token_env = store_config.get('telegram_bot_token_env')
    store_bot_token = (
        store_config.get('telegram_bot_token') or 
        (os.environ.get(store_token_env) if store_token_env else None) or 
        global_bot_token
    )
    
    if not raw_domain or not chat_id:
        print(f"Skipping incomplete store config: {store_config}", flush=True)
        return

    if not store_bot_token:
        print(f"[{raw_domain}] Warning: No Telegram bot token configured for this store or globally.", flush=True)

    domain = get_store_domain(raw_domain)
    clean_domain = domain.replace('.', '_')
    state_file = os.path.join(state_dir, f"{clean_domain}_state.json")

    # Load previous state snapshot
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

    all_catalog_variants = fetch_catalog(domain, token)
    if not all_catalog_variants:
        print(f"[{domain}] No active variants found.", flush=True)
        return

    # Smart Filtering: Only query Cart API for items that are availableForSale OR had stock > 0 in previous_state
    variants_to_check = []
    known_v_ids = set()

    for v in all_catalog_variants:
        v_id = v['variant_id']
        known_v_ids.add(v_id)
        prev_stock = previous_state.get(v_id, {}).get('stock', 0) if previous_state else 0
        is_available = v.get('available_for_sale', True)
        
        # Check stock via Cart API if item is available OR was previously in stock > 0
        if is_available or prev_stock > 0 or not previous_state:
            variants_to_check.append(v)

    # Preserve any variant previously tracked in state
    if previous_state:
        for prev_id, prev_data in previous_state.items():
            if prev_id not in known_v_ids and isinstance(prev_data, dict):
                if prev_data.get('stock', 0) > 0:
                    variants_to_check.append(prev_data)
                    all_catalog_variants.append(prev_data)

    print(f"[{domain}] Querying Cart API for {len(variants_to_check)} active/in-stock variants out of {len(all_catalog_variants)} total.", flush=True)
    stock_results = check_stock_in_batches(domain, token, variants_to_check)
    
    current_state = {}
    sales_detected = []
    
    for v in all_catalog_variants:
        v_id = v['variant_id']
        curr_stock = stock_results.get(v_id, 0 if not v.get('available_for_sale', True) else -1)
        
        # If API fetch failed for this variant (-1), retain previous stock level
        if curr_stock == -1 and previous_state and v_id in previous_state:
            curr_stock = previous_state[v_id].get('stock', -1)
            
        v['stock'] = curr_stock
        
        # Preserve previous last_alerted_stock memory if present
        prev_alerted = previous_state.get(v_id, {}).get('last_alerted_stock', None) if previous_state else None
        if prev_alerted is not None:
            v['last_alerted_stock'] = prev_alerted

        if previous_state and v_id in previous_state and curr_stock >= 0:
            prev_stock = previous_state[v_id].get('stock', 0)
            
            # Memory Guard: Never alert again for the exact same stock level
            if prev_alerted is not None and curr_stock == prev_alerted:
                current_state[v_id] = v
                continue
                
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
            # Record detected sales to daily accounting file
            record_daily_sales(clean_domain, sales_detected, daily_dir)

        for sale in sales_detected[:15]:
            send_telegram_alert(
                bot_token=store_bot_token,
                chat_id=chat_id,
                store_name=store_name,
                item=sale['item'],
                prev_stock=sale['prev_stock'],
                curr_stock=sale['curr_stock'],
                qty_sold=sale['qty_sold']
            )
            time.sleep(2.5)

    # Save new state snapshot
    os.makedirs(state_dir, exist_ok=True)
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(current_state, f, indent=2)
    print(f"[{domain}] State updated and saved to {state_file}.", flush=True)

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(base_dir, 'config.json')
    state_dir = os.path.join(base_dir, 'state')
    daily_dir = os.path.join(base_dir, 'daily_sales')
    
    if not os.path.exists(config_path):
        print(f"Error: Config file not found at {config_path}", flush=True)
        sys.exit(1)
        
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
        
    token_env_var = config.get('telegram_bot_token_env', 'TELEGRAM_BOT_TOKEN')
    bot_token = os.environ.get(token_env_var) or os.environ.get('TELEGRAM_BOT_TOKEN')
    
    if not bot_token:
        print(f"Warning: Environment variable '{token_env_var}' is not set. Telegram alerts will fail if sales are detected.", flush=True)
        
    stores = config.get('stores', [])
    enabled_stores = [s for s in stores if s.get('enabled', True)]
    
    print(f"Starting Shopify Multi-Store Sales Tracker. Concurrently scanning {len(enabled_stores)} enabled stores in parallel...", flush=True)
    start_time = time.time()
    
    max_workers = min(len(enabled_stores), 5)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_store, store, bot_token, state_dir, daily_dir): store for store in enabled_stores}
        for future in as_completed(futures):
            store = futures[future]
            domain = store.get('domain', 'Unknown')
            try:
                future.result()
            except Exception as e:
                print(f"[{domain}] Unhandled error during store scan: {e}", flush=True)
                
    elapsed = time.time() - start_time
    print(f"All {len(enabled_stores)} store scans completed in {elapsed:.2f} seconds!", flush=True)

if __name__ == '__main__':
    main()
