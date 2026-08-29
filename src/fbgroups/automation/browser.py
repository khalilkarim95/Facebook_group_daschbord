from collections.abc import Generator
from contextlib import contextmanager

from playwright.sync_api import BrowserContext, sync_playwright

from fbgroups.config import AppConfig


@contextmanager
def get_browser_context(
    config: AppConfig, headless: bool = True
) -> Generator[BrowserContext, None, None]:
    """Provides a persistent browser context for Facebook automation.
    
    If headless is False, launches a visible browser (useful for initial login).
    Otherwise, uses the saved state to launch a headless browser.
    """
    state_dir = config.path("data_dir") / "browser_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    
    with sync_playwright() as p:
        # We use launch_persistent_context to keep cookies, local storage, and login state
        context = p.chromium.launch_persistent_context(
            user_data_dir=state_dir,
            headless=headless,
            # Facebook often blocks headless browsers if they don't look like real users
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
            viewport={"width": 1280, "height": 720},
            locale="de-DE",
            timezone_id="Europe/Berlin",
        )
        
        # Optionally block images/media to speed up automated posting
        # (but skip if visible for login)
        if headless:
            context.route("**/*.{png,jpg,jpeg,webp,gif,css,woff2}", lambda route: route.abort())

        try:
            yield context
        finally:
            context.close()
