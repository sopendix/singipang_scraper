import re
import time
import schedule
import datetime
import requests
import os
from playwright.sync_api import sync_playwright

# Configuration
CONFIG = {
    'CLUB_ID': '31420530',
    'MENU_ID': '21',
    'KEYWORD_TITLE_PART': '출석체크',
    'KEYWORD_COMMENT': 'musinsa',
    # Days back mode (Easy)
    'USE_DATE_RANGE': False, # If True, use START_DATE ~ END_DATE. If False, use CHECK_DAYS_BACK.
    'CHECK_DAYS_BACK': 3,

    # Date Range mode (Specific) - format: 'MMDD' (e.g., '0201')
    'START_DATE': '0101', 
    'END_DATE': '0207',

    'SAVE_KEYCODES_ONLY': True, # If True, saves extracted keyCodes to 'keycodes.txt' locally
    'GAS_WEB_APP_URL': 'https://script.google.com/macros/s/AKfycbwS1Fq4h77mJ74hEKIv76z_CVqEtnRRhimOCFMeIm1kmlQiuweatyr0BmtJ9oQQexgA/exec'
}

def get_date_range():
    if not CONFIG['USE_DATE_RANGE']:
        # Existing logic: Today ~ N days back
        dates = []
        for i in range(CONFIG['CHECK_DAYS_BACK']):
            date = datetime.datetime.now() - datetime.timedelta(days=i)
            dates.append(date.strftime('%m%d'))
        return dates
    else:
        # Range logic: START_DATE ~ END_DATE
        dates = []
        start_str = CONFIG['START_DATE']
        end_str = CONFIG['END_DATE']
        
        # Assume current year (careful around year boundary, but simplistic for now)
        year = datetime.datetime.now().year
        start_dt = datetime.datetime.strptime(f"{year}{start_str}", "%Y%m%d")
        end_dt = datetime.datetime.strptime(f"{year}{end_str}", "%Y%m%d")
        
        # If end < start (e.g. over new year), adjust start year (not perfect but simple)
        if end_dt < start_dt:
            start_dt = start_dt.replace(year=year-1)

        delta = end_dt - start_dt
        for i in range(delta.days + 1):
            d = start_dt + datetime.timedelta(days=i)
            dates.append(d.strftime('%m%d'))
        
        # Reverse to process latest first? Or asc? Original logic was desc (today -> past).
        # Let's sort descending (latest first) to match original behavior
        dates.sort(reverse=True)
        return dates

def save_keycodes(items):
    km_file = "keycodes.txt"
    with open(km_file, "a", encoding="utf-8") as f:
        count = 0
        for item in items:
            link = item.get('link', '')
            # Extract keyCode=...
            match = re.search(r'keyCode=([^&]+)', link)
            if match:
                code = match.group(1)
                f.write(f"{code}\n")
                count += 1
        print(f"  [Local] Saved {count} keyCodes to {km_file}")

def run_scraper():
    # GitHub Actions Environment Override
    if os.getenv('GITHUB_ACTIONS') == 'true':
        print("  [Info] GitHub Actions detected: Forcing DAILY mode (Latest 2 days).")
        CONFIG['USE_DATE_RANGE'] = False
        CONFIG['CHECK_DAYS_BACK'] = 2 # Check today + yesterday (09:00, 21:00)
    
    print(f"\n[{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Starting Playwright Scan...")
    
    with sync_playwright() as p:
        state_file = "state.json"
        
        # Detect GitHub Actions environment
        headless_mode = os.getenv('GITHUB_ACTIONS') == 'true'
        print(f"  [Info] Headless Mode: {headless_mode}")
        
        browser = p.chromium.launch(headless=headless_mode) 
        
        context_args = {
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "viewport": {"width": 1280, "height": 720}
        }
        if os.path.exists(state_file):
            context_args["storage_state"] = state_file
            print("  [Info] Loaded saved login session.")
            
        context = browser.new_context(**context_args)
        page = context.new_page()

        for mmdd in get_date_range():
            print(f"Checking Date: {mmdd}")
            
            try:
                list_url = f"https://cafe.naver.com/f-e/cafes/{CONFIG['CLUB_ID']}/menus/{CONFIG['MENU_ID']}?viewType=L"
                page.goto(list_url, wait_until="networkidle")
                
                try:
                    page.wait_for_selector("a[href*='articles'], a[href*='articleid']", timeout=10000)
                except:
                    if "로그인" in page.title() or "nid.naver.com" in page.url:
                        print("  [Action Required] Login Page Detected!!")
                        print("  Please LOG IN manually in the opened browser window.")
                        print("  I will wait for 2 minutes...")
                        
                        try:
                            page.wait_for_selector("a[href*='articles'], a[href*='articleid']", timeout=120000)
                            context.storage_state(path=state_file)
                            print("  [Success] Login detected and saved to state.json! Continuing...")
                        except:
                            print("  [Error] Login timeout. Skipping this run.")
                            continue
                    else:
                        print(f"  [Result] List not found. Title: {page.title()}")
                        continue
                
                current_page = 1
                max_pages = 10
                found_for_date = False

                while current_page <= max_pages:
                    try:
                        page.wait_for_selector("a[href*='articles'], a[href*='articleid']", timeout=5000)
                    except:
                        pass # No articles or load error

                    article_id = None
                    links = page.locator("a").all()
                    
                    for link in links:
                        if not link.is_visible(): continue
                        
                        text = link.inner_text().strip()
                        href = link.get_attribute("href") or ""
                        
                        if CONFIG['KEYWORD_TITLE_PART'] in text and mmdd in text:
                            match = re.search(r'articles/(\d+)', href) or re.search(r'articleid=(\d+)', href)
                            
                            if match:
                                article_id = match.group(1)
                                print(f"  [Found] Article ID: {article_id} | Title: {text}")
                                found_for_date = True
                                break
                                
                    if found_for_date:
                        process_article(page, article_id, mmdd)
                        break
                    
                    # If not found, try next page
                    print(f"  [Info] Page {current_page}: Article not found. Checking next page...")
                    
                    # Click next page button (e.g., '2', '3'...)
                    next_page_num = current_page + 1
                    next_btn = page.locator(f"div.Pagination button.btn.number:has-text('{next_page_num}')")
                    
                    # The button text might be exactly the number, use exact match if possible or filter
                    # Using :text-is might be safer if available, but :has-text is standard
                    # The user provided HTML shows <button ...>2</button>
                    
                    if next_btn.count() > 0 and next_btn.first.is_visible():
                        print(f"  [Action] Clicking Page {next_page_num}...")
                        next_btn.first.click()
                        time.sleep(2) # Wait for AJAX load
                        current_page += 1
                    else:
                        print("  [Info] No more pages or next button not found.")
                        break

                if not found_for_date:
                    print(f"  -> No post found for {mmdd} after checking {current_page} pages.")
                    
            except Exception as e:
                print(f"  [Error] Processing {mmdd}: {e}")
        
        browser.close()
    
    print("Scan Complete.")

def extract_sheet_label(page):
    """Extract only the text between '정답' and '1.' for GAS sheet naming."""
    try:
        text_blocks = page.locator("div.se-module.se-module-text").all_inner_texts()
        if not text_blocks:
            return ""

        merged_text = re.sub(r"\s+", " ", " ".join(text_blocks)).strip()
        if not merged_text:
            return ""

        # Capture only the label text between '정답' and '1.'.
        answer_match = re.search(r"\uc815\ub2f5\s*[:：]?\s*(.*?)\s*1\.", merged_text, re.DOTALL)
        if answer_match:
            return re.sub(r"\s+", " ", answer_match.group(1)).strip()

        return ""
    except Exception as e:
        print(f"  [Warn] Failed to extract sheet label: {e}")
        return ""

def process_article(page, article_id, mmdd):
    print(f"  [Comments] Parsing Article {article_id}...")
    
    article_url = f"https://cafe.naver.com/ca-fe/cafes/{CONFIG['CLUB_ID']}/articles/{article_id}"
    page.goto(article_url, wait_until="domcontentloaded")
    
    sheet_label = extract_sheet_label(page)
    if sheet_label:
        print(f"  [TitleHint] Extracted sheet label ({len(sheet_label)} chars).")
    else:
        print("  [TitleHint] Sheet label not found; GAS will use MMDD only.")

    found_links = []
    
    try:
        # Increase timeout for comments to load (5s -> 15s)
        page.wait_for_selector(".CommentBox, .CommentItem, .comment_box", timeout=15000)
        time.sleep(1) 
        
        for page_num in range(1, 6):
            print(f"    [Page {page_num}] Scanning comments...")
            
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2) # Increase wait after scroll

            comments = page.locator("ul.comment_list > li").all()
            if len(comments) == 0:
                 comments = page.locator(".CommentItem").all()
            
            for comment in comments:
                try:
                    if not comment.is_visible(): continue
                    
                    # Extract text only from comment_text_box to avoid getting nicknames
                    text_box = comment.locator("div.comment_text_box")
                    if text_box.count() == 0:
                        continue
                    
                    text_content = text_box.first.inner_text()
                    
                    if CONFIG['KEYWORD_COMMENT'] in text_content.lower():
                        urls = re.findall(r'(https?://[^\s]+)', text_content)
                        for link in urls:
                            found_links.append({
                                'link': link,
                                'content': text_content,
                                'author': 'Unknown'
                            })
                except:
                    continue
            
            # Comment pagination is under div.CommentBox div.ArticlePaginate
            next_btn = page.locator("div.CommentBox div.ArticlePaginate button.btn.number[aria-pressed='false']").first
            
            if next_btn.count() > 0 and next_btn.is_visible():
                print("    [Info] Found next page button. Clicking...")
                next_btn.click()
                time.sleep(2)
            else:
                print("    [Info] No more pages.")
                break
                
        print(f"  [Comments] Found Total {len(found_links)} links (across pages).")
        
        # Save keycodes locally if config enabled
        if CONFIG.get('SAVE_KEYCODES_ONLY'):
            save_keycodes(found_links)
            
        send_to_gas(found_links, mmdd, sheet_label)
        
    except Exception as e:
        print(f"  [Error] Reading comments: {e}")

def send_to_gas(items, mmdd, sheet_label=""):
    if not items:
        return
    
    payload = {
        'date': mmdd,
        'items': items,
        'sheetLabel': sheet_label
    }
    
    try:
        response = requests.post(CONFIG['GAS_WEB_APP_URL'], json=payload, timeout=120)
        print(f"  [Upload] Sent to GAS: {response.text}")
    except Exception as e:
        print(f"  [Error] Sending to GAS: {e}")

if __name__ == "__main__":
    print("=== Naver Cafe Playwright Scraper ===")
    
    run_scraper()
    
    # Exit if running in GitHub Actions (to avoid infinite loop)
    if os.getenv('GITHUB_ACTIONS') == 'true':
        print("  [Info] GitHub Actions execution complete. Exiting.")
    else:
        # Local loop mode
        schedule.every(1).hours.do(run_scraper)
        
        while True:
            schedule.run_pending()
            time.sleep(1)


