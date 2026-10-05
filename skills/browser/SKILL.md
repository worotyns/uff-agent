# Browser – Cloudflare Browser Run

Browser automation via Cloudflare Browser Run API. No local browser needed.

## Tools
- `browser_screenshot(url)` – capture page screenshot, saved as attachment
- `browser_markdown(url)` – extract page content as Markdown
- `browser_crawl(url, depth)` – crawl a website (async job)

## Configuration
Needs `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` in `.env`.
