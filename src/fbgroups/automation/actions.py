import random
import re
from enum import StrEnum

from playwright.sync_api import BrowserContext
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from rich.console import Console

console = Console()

# Die Beschriftungen, unter denen Facebook dieselben Knoepfe je nach
# Oberflaechensprache fuehrt. Sie stehen hier und nicht im Selektor, weil sie
# an drei Stellen gebraucht werden - und weil eine fehlende Sprache dann eine
# Zeile ist und keine Suche durch den ganzen Ablauf.
#
# ``:has-text()`` vergleicht als Teilzeichenkette ("Schreib etwas ..." trifft
# also), ``[aria-label='...']`` dagegen genau - deshalb stehen unten die
# vollen Beschriftungen.
SCHREIB_ETWAS = ("Schreib etwas", "Write something", "اكتب شيئًا")
POSTEN = ("Posten", "Post", "نشر")
GEFAELLT_MIR = ("Gefällt mir", "Like", "أعجبني")

# Das Schreibfeld. ``_DIALOG_TEXTFELD`` hat Vorrang: Der Klick auf "Schreib
# etwas" oeffnet einen Dialog, und ein Feld ausserhalb davon gehoert zu einem
# anderen Zweck - etwa dem Kommentarfeld eines fremden Beitrags im Strom.
_DIALOG = "div[role='dialog']"
_TEXTFELD = "div[role='textbox'][contenteditable='true']"
_DIALOG_TEXTFELD = f"{_DIALOG} {_TEXTFELD}"

# Das Kommentarfeld **am Beitrag**, an seiner Beschriftung erkannt. Das ``i``
# macht den Vergleich unabhaengig von der Gross-/Kleinschreibung; als
# Teilzeichenkette trifft es auch "Schreibe einen oeffentlichen Kommentar ...".
# "Kommentar" und "comment" sind beide noetig - das eine ist im anderen nicht
# enthalten.
_KOMMENTARFELD = ", ".join(
    f"{_TEXTFELD}[aria-label*='{wort}' i]" for wort in ("Kommentar", "comment", "تعليق")
)


def post_to_group(context: BrowserContext, group_url: str, text: str) -> bool:
    """Automates posting a message to a Facebook group."""
    page = context.new_page()
    try:
        console.print(f"Navigating to {group_url}...")
        page.goto(group_url, wait_until="domcontentloaded", timeout=60000)

        # Human-like warm-up: scroll randomly before attempting to post
        page.wait_for_timeout(random.randint(2000, 4000))
        page.evaluate(f"window.scrollBy(0, {random.randint(300, 800)})")
        page.wait_for_timeout(random.randint(1500, 3000))
        page.evaluate(f"window.scrollBy(0, -{random.randint(100, 400)})")
        page.wait_for_timeout(random.randint(1000, 2000))

        # 1. Find the "Write something" trigger button
        console.print("Looking for the 'Write something' box...")
        # Ein Locator ist immer wahr - er ist ein Handgriff, kein gefundenes
        # Element. Eine Kette mit ``or`` nimmt deshalb stets das erste Glied,
        # und alle weiteren waeren toter Code: Stuende die Oberflaeche auf
        # Englisch, fiele die Automatisierung aus, ohne dass jemand den Grund
        # saehe. Die Sprachen gehoeren darum in **einen** Selektor.
        create_post_trigger = page.locator(
            ", ".join(
                f"div[role='button']:has-text('{wort}')" for wort in SCHREIB_ETWAS
            )
        ).first

        try:
            create_post_trigger.wait_for(state="visible", timeout=10000)
            create_post_trigger.click(delay=random.randint(100, 300))
        except PlaywrightTimeoutError:
            console.print(
                "[red]Could not find the 'Write something' button. "
                "Are you logged in and a member of the group?[/red]"
            )
            return False

        page.wait_for_timeout(random.randint(1500, 3000))

        # 2. Find the actual text box
        console.print("Focusing the text area...")
        # Der Klick oben oeffnet einen Dialog, und **darin** steht das Feld.
        # Ohne diese Eingrenzung nimmt ``.first`` das erste contenteditable im
        # ganzen Dokument - auf einer Gruppenseite ist das oft ein verstecktes
        # Feld aus dem Beitragsstrom, das nie sichtbar wird. Der Lauf lief
        # dann in den Zeitablauf und meldete "kein Textfeld", obwohl der
        # Dialog offen davorstand.
        #
        # Bewusst eine Reihenfolge aus zwei Versuchen und **kein** Selektor
        # mit Komma: Bei einer Kommaliste entscheidet die Stellung im DOM,
        # welches Feld ``.first`` erwischt - hier soll aber der Dialog den
        # Vorrang haben, ganz gleich wo er steht.
        textbox = None
        for beschreibung, locator, frist in (
            ("im Dialog", page.locator(_DIALOG_TEXTFELD).first, 20000),
            ("auf der Seite", page.locator(_TEXTFELD).first, 10000),
        ):
            try:
                locator.wait_for(state="visible", timeout=frist)
            except PlaywrightTimeoutError:
                continue
            console.print(f"Textfeld gefunden ({beschreibung}).")
            textbox = locator
            break

        if textbox is None:
            # Zahlen, kein Inhalt: Wie viele Dialoge und Felder die Seite
            # gerade fuehrt, sagt genug fuer die Fehlersuche - ein Beitragstext
            # oder ein Name waere eine Grenzverletzung.
            console.print(
                "[red]Could not find the post text area.[/red] "
                f"(Dialoge: {page.locator(_DIALOG).count()}, "
                f"Textfelder: {page.locator(_TEXTFELD).count()})"
            )
            return False

        try:
            page.wait_for_timeout(random.randint(500, 1500))
            textbox.click(delay=random.randint(100, 300))
            page.wait_for_timeout(random.randint(500, 1000))
            # Simulate human typing
            console.print("Typing message (pasting/inserting directly)...")
            page.keyboard.insert_text(text)
        except PlaywrightTimeoutError:
            console.print("[red]Textfeld gefunden, aber nicht beschreibbar.[/red]")
            return False

        page.wait_for_timeout(random.randint(800, 2000))

        # 3. Find and click the Submit/Post button
        console.print("Submitting post...")
        # Dieselbe Falle wie oben: ein Selektor statt einer ``or``-Kette.
        submit_button = page.locator(
            ", ".join(f"div[aria-label='{wort}']" for wort in POSTEN)
        ).first

        try:
            # Facebook post buttons are sometimes disabled initially until text registers
            page.wait_for_timeout(random.randint(1500, 3000))
            submit_button.wait_for(state="visible", timeout=10000)
            submit_button.click(delay=random.randint(100, 300))
        except PlaywrightTimeoutError:
            console.print("[red]Could not find the Submit button to post.[/red]")
            return False

        # Wait for the posting to complete (the modal usually closes)
        page.wait_for_timeout(random.randint(4000, 6000))
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

        # Human-like warm-up
        page.wait_for_timeout(random.randint(2000, 4000))
        page.evaluate(f"window.scrollBy(0, {random.randint(300, 800)})")
        page.wait_for_timeout(random.randint(1000, 2500))
        page.evaluate(f"window.scrollBy(0, {random.randint(200, 500)})")
        page.wait_for_timeout(random.randint(1500, 3000))

        # Occasionally 'Like' the post before commenting to build trust
        if random.random() < 0.3:
            try:
                like_btn = page.locator(
                    ", ".join(f"div[aria-label='{wort}']" for wort in GEFAELLT_MIR)
                ).first
                # ``count()`` fragt die Seite; der Locator selbst waere immer wahr.
                if like_btn.count() > 0:
                    like_btn.click(delay=random.randint(100, 300))
                    console.print("Liked the post.")
                    page.wait_for_timeout(random.randint(1500, 3000))
            except Exception:
                pass  # It's okay if it fails

        # Scroll down a bit more to ensure comment box is loaded
        page.evaluate("window.scrollBy(0, 500)")
        page.wait_for_timeout(random.randint(1000, 2000))

        console.print("Looking for the comment box...")
        # Dieselbe Falle wie beim Beitrag, nur umgekehrt herum: ``.last`` nahm
        # das **letzte** contenteditable der Seite. Unter einem Beitrag mit
        # Kommentaren ist das oft das Antwortfeld eines fremden Kommentars -
        # unser Text landete dann als Antwort an eine einzelne Person statt als
        # Kommentar am Beitrag. Er stuende eingeklappt unter einem fremden
        # Wortwechsel, und niemand faende ihn.
        #
        # Das richtige Feld nennt sich selbst: Facebook beschriftet es je nach
        # Sprache. Erst danach, wenn keine Beschriftung passt, die alte Regel.
        comment_box = None
        for beschreibung, locator, frist in (
            ("beschriftet", page.locator(_KOMMENTARFELD).first, 15000),
            ("letztes Feld", page.locator(_TEXTFELD).last, 10000),
        ):
            try:
                locator.wait_for(state="visible", timeout=frist)
            except PlaywrightTimeoutError:
                continue
            console.print(f"Kommentarfeld gefunden ({beschreibung}).")
            comment_box = locator
            break

        if comment_box is None:
            console.print(
                "[red]Could not find the comment box. Are comments allowed on this post?[/red] "
                f"(Textfelder: {page.locator(_TEXTFELD).count()})"
            )
            return False

        try:
            page.wait_for_timeout(random.randint(500, 1500))
            comment_box.click(delay=random.randint(100, 300))
            page.wait_for_timeout(random.randint(500, 1000))
            console.print("Typing comment (pasting/inserting directly)...")
            page.keyboard.insert_text(text)
        except PlaywrightTimeoutError:
            console.print("[red]Kommentarfeld gefunden, aber nicht beschreibbar.[/red]")
            return False

        page.wait_for_timeout(random.randint(800, 2000))
        console.print("Submitting comment (pressing Enter)...")
        # Submitting comments on Facebook is usually just hitting Enter
        comment_box.press("Enter", delay=random.randint(50, 150))

        page.wait_for_timeout(random.randint(3000, 5000))
        console.print("[green]Comment submitted successfully![/green]")
        return True

    finally:
        page.close()


# Die Beschriftungen des Beitrittsknopfes. Wie bei SCHREIB_ETWAS: an einer
# Stelle, damit eine fehlende Sprache eine Zeile ist und keine Suche.
BEITRETEN = ("Gruppe beitreten", "Beitreten", "Join group", "Join", "انضمام", "انضم")

# Woran eine Beitrittsfrage zu erkennen ist. Solche Gruppen werden
# uebersprungen, nicht beantwortet: Antworten in fremdem Namen zu erfinden ist
# etwas anderes als einen Knopf zu druecken.
BEITRITTSFRAGEN = (
    "Beantworte",
    "Fragen der Gruppe",
    "Answer",
    "membership question",
    "أسئلة",
    "أجب",
)


class Beitrittsausgang(StrEnum):
    """Was der Beitrittsversuch ergeben hat - vier unterscheidbare Faelle.

    ``BEREITS_MITGLIED`` ist kein Fehlschlag, sondern eine Auskunft, die
    ohnehin auf dem Bildschirm stand: Wo der Beitrittsknopf fehlt und das
    Schreibfeld da ist, sind wir drin. Sie mitzunehmen kostet nichts und
    schliesst die Kette - sonst wuesste niemand je, dass eine Freigabe
    gekommen ist.

    ``FRAGEN`` bedeutet: Die Gruppe stellt Beitrittsfragen. Der Versuch wird
    abgebrochen, **nichts** wird abgeschickt.
    """

    ANGEFRAGT = "angefragt"
    BEREITS_MITGLIED = "bereits_mitglied"
    FRAGEN = "fragen"
    FEHLER = "fehler"


def request_join(context: BrowserContext, group_url: str) -> tuple[Beitrittsausgang, str]:
    """Stellt **eine** Beitrittsanfrage. Returns: ``(Ausgang, Bemerkung)``.

    Die riskanteste Handlung des Projekts, und deshalb die vorsichtigste:

    * Gruppen mit Beitrittsfragen werden **uebersprungen**. Eine Antwort in
      deinem Namen zu erfinden waere etwas anderes als einen Knopf zu druecken.
    * Ist der Beitrittsknopf nicht da, wird nichts gesucht und nichts geklickt -
      dann sind wir entweder schon Mitglied oder die Gruppe laesst niemanden.
    * Es wird genau ein Knopf gedrueckt. Kein Formular, kein zweiter Versuch.
    """
    page = context.new_page()
    try:
        console.print(f"Oeffne {group_url} ...")
        page.goto(group_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(random.randint(2000, 4000))

        beitreten = page.locator(
            ", ".join(f"div[role='button']:has-text('{wort}')" for wort in BEITRETEN)
        ).first

        if beitreten.count() == 0:
            # Kein Beitrittsknopf. Steht das Schreibfeld da, sind wir drin -
            # diese Auskunft lag ohnehin auf dem Bildschirm.
            if page.locator(_TEXTFELD).count() > 0 or page.locator(
                ", ".join(f"div[role='button']:has-text('{w}')" for w in SCHREIB_ETWAS)
            ).count() > 0:
                return Beitrittsausgang.BEREITS_MITGLIED, "Schreibfeld vorhanden"
            return Beitrittsausgang.FEHLER, "kein Beitrittsknopf und kein Schreibfeld"

        try:
            beitreten.wait_for(state="visible", timeout=10000)
        except PlaywrightTimeoutError:
            return Beitrittsausgang.FEHLER, "Beitrittsknopf nicht sichtbar"

        beitreten.click(delay=random.randint(100, 300))
        page.wait_for_timeout(random.randint(2500, 4000))

        # Kam ein Dialog mit Fragen? Dann nichts abschicken.
        dialog = page.locator(_DIALOG).first
        if dialog.count() > 0:
            text = (dialog.inner_text() or "")[:400]
            if any(wort.lower() in text.lower() for wort in BEITRITTSFRAGEN):
                console.print("[yellow]Gruppe stellt Beitrittsfragen - uebersprungen.[/yellow]")
                return Beitrittsausgang.FRAGEN, "Gruppe stellt Beitrittsfragen"

        console.print("[green]Beitrittsanfrage gestellt.[/green]")
        return Beitrittsausgang.ANGEFRAGT, ""

    except PlaywrightTimeoutError:
        return Beitrittsausgang.FEHLER, "Zeitablauf"
    finally:
        page.close()


def fetch_group_html(context: BrowserContext, group_url: str) -> str:
    """Holt den HTML **einer** Gruppenseite - angemeldet, damit Zahlen dastehen.

    Nur holen, nicht auswerten: Das tut ``gruppenseite.lies_seite``, und zwar
    dieselbe Funktion wie beim Weg ueber ``httpx``. Zwei Auswertungen koennten
    andere Zahlen liefern, und der Unterschied fiele erst in einer Rangliste
    auf, nach der entschieden wird, wo die naechsten dreihundert Beitraege
    hingehen.

    Gescrollt wird ein Stueck, damit die Beitragsliste nachlaedt - aus ihren
    **Zeitpunkten** entsteht die Aktivitaet. Beitragstexte und Namen werden
    nicht angefasst; ``lies_seite`` nimmt sie gar nicht erst entgegen.
    """
    page = context.new_page()
    try:
        page.goto(group_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(random.randint(2000, 3500))
        for _ in range(3):
            page.evaluate("window.scrollBy(0, 1200)")
            page.wait_for_timeout(random.randint(900, 1600))
        return page.content()
    finally:
        page.close()


def fetch_top_posts(
    context: BrowserContext, group_url: str, group_id: str, limit: int = 5
) -> list[dict]:
    """Scrapes recent posts from the group for metrics (NO TEXT/AUTHORS)."""

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

                from fbgroups.urls import canonical_post_url
                post_url = canonical_post_url(post_url, group_id) or post_url

                from fbgroups.importers.manual_seed import parse_member_count

                # Get text content of the article to parse numbers
                text_content = article.inner_text()

                # Look for comments (German, English, Arabic)
                comments_match = re.search(
                    r"(\d[\d.,\s]*(?:[kKmM]|Tsd\.?|Mio\.?)?)\s*(?:Kommentare?|comments?|تعليقات|تعليق)",
                    text_content,
                    re.IGNORECASE,
                )
                comments_count = (
                    parse_member_count(comments_match.group(1)) if comments_match else 0
                )
                # Fallback on parse_member_count returning None
                comments_count = comments_count or 0

                # Interactions
                reactions_locator = article.locator(
                    "[aria-label*='gefällt das'], [aria-label*='Reaktionen'], "
                    "[aria-label*='likes'], [aria-label*='reactions'], "
                    "[aria-label*='تفاعل'], [aria-label*='إعجاب']"
                ).first
                interactions_count = 0
                # Using wait_for timeout 0 or just counting to see if it exists
                if reactions_locator.count() > 0:
                    aria = reactions_locator.get_attribute("aria-label") or ""
                    num_match = re.search(r"(\d[\d.,\s]*(?:[kKmM]|Tsd\.?|Mio\.?)?)", aria)
                    if num_match:
                        interactions_count = parse_member_count(num_match.group(1)) or 0

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
