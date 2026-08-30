from collections.abc import Generator
from contextlib import contextmanager

from playwright.sync_api import BrowserContext, sync_playwright

from fbgroups.config import AppConfig


@contextmanager
def get_browser_context(
    config: AppConfig, headless: bool = False
) -> Generator[BrowserContext, None, None]:
    """Provides a persistent browser context for Facebook automation.
    
    If headless is False (default), launches a visible browser.
    """
    state_dir = config.path("data_dir") / "browser_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    
    with sync_playwright() as p:
        # We use launch_persistent_context to keep cookies, local storage, and login state
        context = p.chromium.launch_persistent_context(
            user_data_dir=state_dir,
            headless=headless,
            viewport={"width": 1280, "height": 720},
            locale="de-DE",
            timezone_id="Europe/Berlin",
        )
        
        # Optionally block images/media to speed up automated posting
        if headless:
            context.route("**/*.{png,jpg,jpeg,webp,gif}", lambda route: route.abort())

        try:
            yield context
        finally:
            context.close()
