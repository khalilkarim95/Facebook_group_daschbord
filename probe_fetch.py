from fbgroups.automation.actions import fetch_top_posts
from fbgroups.automation.browser import get_browser_context
from fbgroups.config import load_config

config = load_config()

with get_browser_context(config, headless=True) as context:
    url = "https://www.facebook.com/groups/1060505847347081"
    
    print(f"Fetching posts from {url}...")
    posts = fetch_top_posts(context, url, "1060505847347081", limit=3)
    
    print("Found posts:")
    for post in posts:
        print(post)

