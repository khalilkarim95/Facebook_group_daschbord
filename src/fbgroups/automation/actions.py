from playwright.sync_api import BrowserContext
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from rich.console import Console

console = Console()


def post_to_group(context: BrowserContext, group_url: str, text: str) -> bool:
    """Automates posting a message to a Facebook group."""
    page = context.new_page()
    try:
        console.print(f"Navigating to {group_url}...")
        page.goto(group_url, wait_until="domcontentloaded", timeout=60000)

        # Give page some time to render completely
        page.wait_for_timeout(3000)

        # 1. Find the "Write something" trigger button
        console.print("Looking for the 'Write something' box...")
        # Try multiple locators for the post creation box.
        # Facebook uses various languages and aria labels.
        create_post_trigger = (
            page.locator("div[role='button']:has-text('Schreib etwas')").first
            or page.locator("div[role='button']:has-text('Write something')").first
            or page.locator("div:text-matches('(?i)(Schreib etwas|Write something)')").last
        )

        try:
            create_post_trigger.wait_for(state="visible", timeout=10000)
            create_post_trigger.click()
        except PlaywrightTimeoutError:
            console.print(
                "[red]Could not find the 'Write something' button. "
                "Are you logged in and a member of the group?[/red]"
            )
            return False

        page.wait_for_timeout(2000)

        # 2. Find the actual text box
        console.print("Focusing the text area...")
        textbox = page.locator("div[role='textbox'][contenteditable='true']").first

        try:
            textbox.wait_for(state="visible", timeout=10000)
            textbox.click()
            # Simulate human typing
            console.print("Typing message...")
            for char in text:
                textbox.press(char)
                page.wait_for_timeout(50)  # 50ms between keystrokes
        except PlaywrightTimeoutError:
            console.print("[red]Could not find the post text area.[/red]")
            return False

        page.wait_for_timeout(1000)

        # 3. Find and click the Submit/Post button
        console.print("Submitting post...")
        submit_button = (
            page.locator("div[role='button'][aria-label='Posten']").first
            or page.locator("div[role='button'][aria-label='Post']").first
            or page.get_by_role("button", name="Posten").first
            or page.get_by_role("button", name="Post").first
        )

        try:
            # Facebook post buttons are sometimes disabled initially until text registers
            page.wait_for_timeout(2000)
            submit_button.wait_for(state="visible", timeout=10000)
            submit_button.click()
        except PlaywrightTimeoutError:
            console.print("[red]Could not find the Submit button to post.[/red]")
            return False

        # Wait for the posting to complete (the modal usually closes)
        page.wait_for_timeout(5000)
        console.print("[green]Post submitted successfully![/green]")
        return True

    finally:
        page.close()


def comment_on_post(context: BrowserContext, post_url: str, text: str) -> bool:
    """Automates commenting on a specific Facebook post."""
    page = context.new_page()
    try:
        console.print(f"Navigating to post {post_url}...")
        page.goto(post_url, wait_until="domcontentloaded", timeout=60000)

        page.wait_for_timeout(3000)

        # Scroll down a bit to ensure comment box is loaded
        page.evaluate("window.scrollBy(0, 500)")
        page.wait_for_timeout(1000)

        console.print("Looking for the comment box...")
        comment_box = page.locator("div[role='textbox'][contenteditable='true']").last

        try:
            comment_box.wait_for(state="visible", timeout=15000)
            comment_box.click()
            console.print("Typing comment...")
            for char in text:
                comment_box.press(char)
                page.wait_for_timeout(50)
        except PlaywrightTimeoutError:
            console.print(
                "[red]Could not find the comment box. Are comments allowed on this post?[/red]"
            )
            return False

        page.wait_for_timeout(1000)
        console.print("Submitting comment (pressing Enter)...")
        # Submitting comments on Facebook is usually just hitting Enter
        comment_box.press("Enter")

        page.wait_for_timeout(3000)
        console.print("[green]Comment submitted successfully![/green]")
        return True

    finally:
        page.close()


def fetch_top_posts(
    context: BrowserContext, group_url: str, group_id: str, limit: int = 5
) -> list[dict]:
    """Scrapes recent posts from the group for metrics (NO TEXT/AUTHORS)."""
    import re

    page = context.new_page()
    posts_data = []
    try:
        console.print(f"Navigating to {group_url} to fetch posts...")
        page.goto(group_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        # Scroll a few times to load posts
        for _ in range(3):
            page.evaluate("window.scrollBy(0, 1000)")
            page.wait_for_timeout(1500)

        articles = page.locator("div[role='article']").all()
        console.print(f"Found {len(articles)} articles.")

        for article in articles:
            if len(posts_data) >= limit:
                break

            try:
                # FB post links often contain 'multi_permalinks' or 'permalink' or 'posts'
                links = article.locator("a[href*='/groups/']").all()
                post_url = None
                for link in links:
                    href = link.get_attribute("href")
                    if href and (
                        "/permalink/" in href or "/posts/" in href or "multi_permalinks" in href
                    ):
                        post_url = href
                        break

                if not post_url:
                    continue

                # Fix relative URLs
                if post_url.startswith("/"):
                    post_url = "https://www.facebook.com" + post_url

                # Remove query params to get clean URL (unless it's multi_permalinks)
                if "?" in post_url and "multi_permalinks" not in post_url:
                    post_url = post_url.split("?")[0]

                # Get text content of the article to parse numbers
                text_content = article.inner_text()

                # Look for comments
                comments_match = re.search(r"(\d+)\s*Kommentar", text_content, re.IGNORECASE)
                comments_count = int(comments_match.group(1)) if comments_match else 0

                # Interactions
                reactions_locator = article.locator(
                    "[aria-label*='gefällt das'], [aria-label*='Reaktionen']"
                ).first
                interactions_count = 0
                # Using wait_for timeout 0 or just counting to see if it exists
                if reactions_locator.count() > 0:
                    aria = reactions_locator.get_attribute("aria-label") or ""
                    num_match = re.search(r"(\d+)", aria)
                    if num_match:
                        interactions_count = int(num_match.group(1))

                posts_data.append(
                    {
                        "post_url": post_url,
                        "interactions": interactions_count,
                        "comments": comments_count,
                    }
                )
            except Exception as e:
                console.print(f"Error parsing article: {e}")
                continue

    finally:
        page.close()

    return posts_data
