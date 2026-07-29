# Shopify Multi-Website Real-Time Sales Tracker

Automated Shopify inventory and sales monitoring system. It maps store inventory levels every 5 minutes, detects stock reductions as live sales, and dispatches automated Telegram photo cards (product image, title, price, sold quantity, remaining stock, and product URL) directly to individual website sales channels.

---

## Features
- **Multi-Store Support**: Track multiple Shopify stores simultaneously.
- **Dedicated Telegram Channel Routing**: Post sales alerts from Website A to Channel A, Website B to Channel B, etc.
- **Rich Telegram Sales Cards**: Automatically includes product image, title, variant, price, exact quantity sold, updated stock level, and product link.
- **5-Minute Cloud Automation**: Powered by GitHub Actions cron with state persistence.
- **Zero API Keys Required for Shopify**: Harvests public Storefront tokens automatically.

---

## Setup Instructions

### 1. Create a Telegram Bot
1. Open Telegram and search for `@BotFather`.
2. Send `/newbot` and follow the prompts to create your bot.
3. Save the **HTTP API Token** provided (e.g., `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`).

---

### 2. Create Channels & Add Your Bot as Admin
1. Create a new Telegram Channel for each Shopify website you want to track (e.g., `MyStore1 Sales Alerts`).
2. Open channel settings -> **Administrators** -> **Add Admin** -> Search for your bot handle and add it.
3. Ensure the bot has **Post Messages** permission enabled.
4. Note down the Channel Username (e.g., `@mystore1_sales`) or Chat ID.

---

### 3. Configure `config.json`
Edit [config.json](file:///C:/Users/Yash%20Chavan/.gemini/antigravity/scratch/shopify-sales-tracker/config.json) to add your target websites and their corresponding Telegram Channel usernames/IDs:

```json
{
  "telegram_bot_token_env": "TELEGRAM_BOT_TOKEN",
  "stores": [
    {
      "name": "Dulhan Jewels",
      "domain": "dulhanjewels.com",
      "telegram_chat_id": "@dulhan_sales_channel",
      "enabled": true
    },
    {
      "name": "Rasa Silver",
      "domain": "rasasilver.com",
      "telegram_chat_id": "@rasa_sales_channel",
      "enabled": true
    }
  ]
}
```

---

### 4. Deploy to GitHub
1. Push this project folder to your private or public GitHub repository.
2. In your GitHub repository:
   - Go to **Settings** -> **Secrets and variables** -> **Actions**.
   - Click **New repository secret**.
   - **Name**: `TELEGRAM_BOT_TOKEN`
   - **Secret**: Paste your Telegram Bot Token from Step 1.
3. Go to **Settings** -> **Actions** -> **General** -> **Workflow permissions**.
   - Select **Read and write permissions** (allows the workflow to commit updated stock state back to the repository).

---

## How It Works
1. Every 5 minutes, GitHub Actions runs `shopify_sales_tracker.py`.
2. The script scrapes each enabled Shopify store's active inventory and product image URLs.
3. It compares current stock against the saved state snapshot in `state/<domain>_state.json`.
4. If `previous_stock > current_stock`, it calculates `sold_qty = previous_stock - current_stock` and sends a Telegram photo card to that store's channel.
5. It commits the updated stock state to the repository so subsequent runs compare against the fresh snapshot.
