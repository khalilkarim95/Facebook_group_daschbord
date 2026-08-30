import pytest
from unittest.mock import MagicMock
from fbgroups.automation.actions import fetch_top_posts

def test_fetch_top_posts_extracts_correct_metrics():
    """Prüft, ob fetch_top_posts Ziffern in DE, EN und AR korrekt ausliest."""
    
    mock_context = MagicMock()
    mock_page = MagicMock()
    mock_context.new_page.return_value = mock_page
    
    class MockLocator:
        def __init__(self, elements_data):
            self.elements_data = elements_data
            
        def all(self):
            return [MockElement(data) for data in self.elements_data]
            
    class MockElement:
        def __init__(self, data):
            self.data = data
            
        def locator(self, selector):
            if "a[href*='/groups/']" in selector:
                # Das ist der Link
                class LinkElement:
                    def __init__(self, data):
                        self.data = data
                    def get_attribute(self, attr):
                        return self.data.get("link")
                
                class LinkLocator:
                    def __init__(self, data):
                        self.data = data
                    @property
                    def first(self):
                        return LinkElement(self.data)
                    def all(self):
                        return [LinkElement(self.data)]
                return LinkLocator(self.data)
            
            if "aria-label*='gefällt das'" in selector or "reactions" in selector or "إعجاب" in selector:
                class ReactElement:
                    def __init__(self, data):
                        self.data = data
                    def count(self):
                        return 1 if self.data.get("html") else 0
                    def get_attribute(self, attr):
                        if attr == "aria-label":
                            html = self.data.get("html", "")
                            if 'aria-label="' in html:
                                return html.split('aria-label="')[1].split('"')[0]
                        return ""
                    def inner_text(self):
                        return self.data.get("reactions_text", "")
                        
                class ReactLocator:
                    def __init__(self, data):
                        self.data = data
                    @property
                    def first(self):
                        return ReactElement(self.data)
                    def all(self):
                        return [ReactElement(self.data)] if self.data.get("reactions_text") else []
                return ReactLocator(self.data)
            
            return MagicMock()
            
        def inner_text(self):
            return self.data.get("full_text", "")
            
        def inner_html(self):
            # for aria-label regex search fallback
            return self.data.get("html", "")
            
    # Wir bauen das Mock so, dass page.locator("div[role='article']") die Mock-Elemente zurückgibt.
    elements_data = [
        {
            "link": "https://facebook.com/groups/123/posts/1/",
            "reactions_text": "Ali, Bernd und 1.2K weiteren gefällt das",
            "full_text": "Tolles Bild!\\n3,5 Tsd. Kommentare",
            "html": 'aria-label="Ali, Bernd und 1.2K weiteren gefällt das"'
        },
        {
            "link": "https://facebook.com/groups/123/posts/2/",
            "reactions_text": "",
            "full_text": "English text\\n3 comments",
            "html": 'aria-label="4.5K likes"'
        },
        {
            "link": "https://facebook.com/groups/123/posts/3/",
            "reactions_text": "",
            "full_text": "نص عربي\\n١٢٠ تعليق",
            "html": 'aria-label="٥٠٠ إعجاب"'
        },
        {
            "link": "https://facebook.com/groups/123/posts/4/",
            "reactions_text": "",
            "full_text": "Just a link without metrics",
            "html": ""
        }
    ]
    
    mock_page.locator.return_value = MockLocator(elements_data)
    
    posts = fetch_top_posts(mock_context, "https://facebook.com/groups/123", "123", limit=4)
    
    # 4 Posts sollten gefunden werden
    assert len(posts) == 4
    
    # 1. Post: 1.2K (1200) reactions, 3,5 Tsd (3500) comments
    assert posts[0]["post_url"].endswith("/posts/1/")
    assert posts[0]["interactions"] == 1200
    assert posts[0]["comments"] == 3500
    
    # 2. Post: 4.5K (4500) reactions, 3 comments
    assert posts[1]["post_url"].endswith("/posts/2/")
    assert posts[1]["interactions"] == 4500
    assert posts[1]["comments"] == 3
    
    # 3. Post: 500 reactions, 120 comments
    assert posts[2]["post_url"].endswith("/posts/3/")
    
