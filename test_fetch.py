import asyncio
from playwright.sync_api import sync_playwright
from fbgroups.automation.actions import fetch_top_posts
from fbgroups.models import GroupPost
from fbgroups.storage.sqlite_store import SqliteStore

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(locale="de-DE")
    
    # We fetch a group
    url = "https://www.facebook.com/groups/1060505847347081"
    
    print(f"Fetching posts from {url}...")
    posts = fetch_top_posts(context, url, "1060505847347081", limit=3)
    
    print("Found posts:")
    for post in posts:
        print(post)
    
    browser.close()
