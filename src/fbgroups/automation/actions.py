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
                page.wait_for_timeout(50) # 50ms between keystrokes
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
                "[red]Could not find the comment box. "
                "Are comments allowed on this post?[/red]"
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
