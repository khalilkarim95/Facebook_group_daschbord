import random
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from playwright.sync_api import BrowserContext
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from rich.console import Console

console = Console()

GEFAELLT_MIR = ("Gefällt mir", "Like", "أعجبني")

_TEXTFELD = "div[role='textbox'][contenteditable='true']"

_KOMMENTARFELD = ", ".join(
    f"{_TEXTFELD}[aria-label*='{wort}' i]" for wort in ("Kommentar", "comment", "تعليق")
)


GRUPPENLIMIT = (
    "limit fuer freizugebende", "limit für freizugebende",
    "limit fuer inhalte", "limit für inhalte",
    "freizugebende inhalte",
    "limit for content to be approved", "limit of pending",
    "pending posts limit", "too many pending",
    "وصلت إلى الحد", "وصلت الى الحد", "الحد الأقصى للمحتوى", "بانتظار الموافقة",
)

AUSSTEHEND = (
    "ausstehend", "wartet auf genehmigung", "wird ueberprueft", "wird überprüft",
    "pending", "awaiting approval", "being reviewed",
    "قيد المراجعة", "بانتظار الموافقة", "في انتظار",
)


BEITRAG_WEG = (
    "هذا المحتوى غير متوفر", "المحتوى غير متوفر", "هذا المحتوى غير متاح",
    "content isn't available", "content is no longer available",
    "this content isn't available right now",
    "inhalt ist derzeit nicht verfuegbar", "inhalt ist derzeit nicht verfügbar",
    "dieser inhalt ist nicht verfuegbar", "dieser inhalt ist nicht verfügbar",
    "seite nicht gefunden", "page not found",
)


ANMELDEWAND = (
    "log in to facebook", "log into facebook", "you must log in",
    "bei facebook anmelden", "in facebook einloggen", "du musst dich anmelden",
    "passwort vergessen", "forgot password", "forgotten password",
    "create new account", "neues konto erstellen",
    "تسجيل الدخول إلى فيسبوك", "تسجيل الدخول الى فيسبوك",
    "إنشاء حساب جديد", "نسيت كلمة السر", "نسيت كلمة المرور",
)

NICHT_ANGEMELDET = "nicht angemeldet"


@dataclass(frozen=True)
class Kommentarausgang:
    """Was aus einem abgeschickten Kommentar geworden ist.

    Frueher war das ein ``bool``, und der war **immer wahr**: Die Funktion
    drueckte Enter, wartete drei Sekunden und meldete Erfolg - ohne die Seite
    danach anzusehen. Am 12.09.2026 fiel auf, was das verschweigt: In einer
    Gruppe mit Freigabepflicht standen die Kommentare als "Ausstehend" und
    waren fuer niemanden sichtbar, waehrend der Lauf sie als veroeffentlicht
    zaehlte.

    Drei unterscheidbare Ausgaenge statt einem:

    * **abgeschickt und sichtbar** - der Normalfall.
    * **abgeschickt, wartet auf Freigabe** - ein Erfolg mit Vorbehalt. Der
      Tracking-Link ist heraus, aber noch klickt ihn niemand.
    * **gar nicht angenommen** - die Gruppe nimmt gerade nichts mehr
      (``gruppenlimit``). Das ist kein Urteil ueber den Text.
    """

    erfolg: bool
    hinweis: str = ""
    wartet_auf_freigabe: bool = False
    gruppenlimit: bool = False
    beitrag_weg: bool = False


def _seitenhinweis(page, muster: tuple[str, ...]) -> str:
    """Sucht eines der Muster im sichtbaren Text - und gibt die Fundstelle.

    Gelesen wird der **Seitentext**, nicht ein Beitrag: Es geht um Facebooks
    eigene Meldung ueber unseren Versuch. Nichts davon wird gespeichert; was
    weitergereicht wird, ist der Hinweis selbst.
    """
    try:
        sichtbar = page.inner_text("body")[:8000].lower()
    except Exception:  # noqa: BLE001 - eine geschlossene Seite ist kein Hinweis
        return ""
    for wort in muster:
        stelle = sichtbar.find(wort.lower())
        if stelle >= 0:
            anfang = max(stelle - 60, 0)
            return " ".join(sichtbar[anfang : stelle + 90].split())
    return ""


def ist_angemeldet(context: BrowserContext) -> tuple[bool, str]:
    """Ist diese Browsersitzung bei Facebook angemeldet? Returns: ``(ja, Hinweis)``.

    **Die Frage vor dem Lauf** (21.09.2026). Sie kostet einen Seitenabruf und
    beantwortet die einzige Vorbedingung, ohne die nichts von dem funktioniert,
    was danach kommt: Eine abgemeldete Sitzung findet kein Kommentarfeld und
    kein Beitragsformular - in **jeder** Gruppe. Jeder dieser Fehlschlaege
    gilt als technisch, und ein technischer Fehlschlag nimmt die Gruppe aus
    der Kampagne. Ohne diese Pruefung raeumte ein abgelaufener Anmeldestand
    eine Kampagne leer, und im Protokoll staende an jeder Gruppe ein Grund,
    an dem nichts liegt.

    Gefragt wird die Startseite und nicht eine Gruppe: Eine Gruppenseite kann
    aus vielen Gruenden nicht laden, die Startseite nur aus einem.

    Bei einem Abruffehler gilt **nicht** "abgemeldet": Ein Netzfehler ist kein
    Beleg fuer eine abgelaufene Sitzung.
    """
    page = context.new_page()
    try:
        page.goto("https://www.facebook.com/", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(1500)
        if hinweis := _seitenhinweis(page, ANMELDEWAND):
            return False, f"{NICHT_ANGEMELDET}: {hinweis}"
        return True, ""
    except Exception as exc:  # noqa: BLE001 - ein Netzfehler ist kein Urteil
        return True, str(exc).splitlines()[0][:120]
    finally:
        page.close()


def _feld_anklicken(page, feld) -> bool:  # noqa: ANN001 - Playwright-Objekte
    """Das Kommentarfeld anklicken - notfalls ein zweites Mal, nach Escape.

    "Kommentarfeld nicht beschreibbar" hiess bis zum 24.09.2026: Der Klick
    lief 30 Sekunden gegen etwas, das darueber lag (ein Hinweisfenster, die
    Reaktionsleiste, ein Tooltip), und der Beitrag galt als gescheitert. Ein
    Mensch drueckt Escape, scrollt das Feld in den Blick und klickt noch
    einmal - genau das, einmal. Danach ist es wirklich nicht beschreibbar.
    """
    try:
        feld.click(delay=random.randint(100, 300), timeout=10000)
        return True
    except PlaywrightTimeoutError:
        pass
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(random.randint(500, 1000))
        feld.scroll_into_view_if_needed(timeout=5000)
        feld.click(delay=random.randint(100, 300), timeout=10000)
        return True
    except PlaywrightTimeoutError:
        return False


def _bild_anhaengen(page, feld, bild) -> bool:  # noqa: ANN001 - Playwright-Objekte
    """Haengt ein Bild an den Kommentar im Entwurf. Returns: ob es angekommen ist.

    Gesucht wird das Dateifeld im **selben Formular** wie das Kommentarfeld -
    sonst landete das Bild womoeglich im Formular fuer einen neuen Beitrag
    oder unter einem fremden Kommentar. Erst danach, wenn das Formular keines
    traegt, das letzte der Seite (das Kommentarfeld steht unten).

    Gewartet wird, bis die Vorschau im Formular steht: Wer vorher absendet,
    schickt den Text ohne Bild - oder Facebook haelt das Absenden an.
    """
    try:
        formular = feld.locator("xpath=ancestor::form[1]")
        im_formular = formular.count() > 0
        eingabe = formular.locator("input[type='file']") if im_formular else None
        if eingabe is None or eingabe.count() == 0:
            eingabe = page.locator("input[type='file']").last
        else:
            eingabe = eingabe.first
        bereich = formular if im_formular else page
        vorher = bereich.locator("img").count()
        eingabe.set_input_files(str(bild), timeout=10000)
        for _ in range(30):
            page.wait_for_timeout(500)
            if bereich.locator("img").count() > vorher:
                page.wait_for_timeout(random.randint(1000, 2000))
                return True
    except Exception as e:  # noqa: BLE001 - ohne Bild geht der Text trotzdem
        console.print(f"[yellow]Bild nicht angehaengt: {e}[/yellow]")
        return False
    console.print("[yellow]Bild hochgeladen, aber keine Vorschau erschienen.[/yellow]")
    return False


def comment_on_post(
    context: BrowserContext, post_url: str, text: str, bild: str | Path | None = None
) -> Kommentarausgang:
    """Automates commenting on a specific Facebook post.

    **Nach dem Absenden wird die Seite gelesen.** Ohne das meldete die
    Funktion jeden Versuch als Erfolg, bei dem das Feld beschreibbar war -
    auch den, den die Gruppe gar nicht angenommen hat.

    **Ein Kommentar mit Tracking-Link geht nicht hinaus** (23.09.2026). Jeder
    Kommentar kommt hier durch, gleich ob aus dem Lauf, aus ``campaign auto``
    oder von der Arbeitsseite - deshalb steht die Pruefung hier und nicht nur
    dort, wo der Text entsteht. Geprueft wird, bevor eine Seite geoeffnet
    wird. Die freie Adresse (``https://b-tarikak.de/home``) ist erlaubt.
    """
    from fbgroups.urls import tracking_adresse_im_text

    if adresse := tracking_adresse_im_text(text):
        console.print(
            f"[red]Kommentar enthaelt eine Tracking-Adresse ({adresse}) - nicht abgesetzt.[/red]"
        )
        return Kommentarausgang(
            erfolg=False, hinweis=f"Adresse im Kommentar ({adresse}) - nicht abgesetzt"
        )

    page = context.new_page()
    try:
        console.print(f"Navigating to post {post_url}...")
        page.goto(post_url, wait_until="domcontentloaded", timeout=60000)

        page.wait_for_timeout(random.randint(2000, 4000))
        page.evaluate(f"window.scrollBy(0, {random.randint(300, 800)})")
        page.wait_for_timeout(random.randint(1000, 2500))
        page.evaluate(f"window.scrollBy(0, {random.randint(200, 500)})")
        page.wait_for_timeout(random.randint(1500, 3000))

        if random.random() < 0.3:
            try:
                like_btn = page.locator(
                    ", ".join(f"div[aria-label='{wort}']" for wort in GEFAELLT_MIR)
                ).first
                if like_btn.count() > 0:
                    like_btn.click(delay=random.randint(100, 300))
                    console.print("Liked the post.")
                    page.wait_for_timeout(random.randint(1500, 3000))
            except Exception:
                pass  # It's okay if it fails

        page.evaluate("window.scrollBy(0, 500)")
        page.wait_for_timeout(random.randint(1000, 2000))

        if hinweis := _seitenhinweis(page, ANMELDEWAND):
            console.print(f"[red]Nicht bei Facebook angemeldet: {hinweis}[/red]")
            return Kommentarausgang(False, hinweis=f"{NICHT_ANGEMELDET}: {hinweis}")

        if hinweis := _seitenhinweis(page, BEITRAG_WEG):
            console.print(f"[yellow]Diesen Beitrag gibt es nicht mehr: {hinweis}[/yellow]")
            return Kommentarausgang(False, hinweis=hinweis, beitrag_weg=True)

        console.print("Looking for the comment box...")
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
            if hinweis := _seitenhinweis(page, GRUPPENLIMIT):
                console.print(f"[yellow]Die Gruppe nimmt gerade nichts mehr an: {hinweis}[/yellow]")
                return Kommentarausgang(False, hinweis=hinweis, gruppenlimit=True)
            console.print(
                "[red]Could not find the comment box. Are comments allowed on this post?[/red] "
                f"(Textfelder: {page.locator(_TEXTFELD).count()})"
            )
            return Kommentarausgang(False, hinweis="Kommentarfeld nicht gefunden")

        page.wait_for_timeout(random.randint(500, 1500))
        if not _feld_anklicken(page, comment_box):
            console.print("[red]Kommentarfeld gefunden, aber nicht beschreibbar.[/red]")
            return Kommentarausgang(False, hinweis="Kommentarfeld nicht beschreibbar")
        try:
            page.wait_for_timeout(random.randint(500, 1000))
            console.print("Typing comment (pasting/inserting directly)...")
            page.keyboard.insert_text(text)
        except PlaywrightTimeoutError:
            console.print("[red]Kommentarfeld gefunden, aber nicht beschreibbar.[/red]")
            return Kommentarausgang(False, hinweis="Kommentarfeld nicht beschreibbar")

        if bild:
            console.print(f"Attaching image {Path(bild).name}...")
            if _bild_anhaengen(page, comment_box, bild):
                console.print("Bild angehaengt.")

        page.wait_for_timeout(random.randint(800, 2000))
        console.print("Submitting comment (pressing Enter)...")
        comment_box.press("Enter", delay=random.randint(50, 150))

        page.wait_for_timeout(random.randint(3000, 5000))

        if hinweis := _seitenhinweis(page, GRUPPENLIMIT):
            console.print(f"[yellow]Die Gruppe nimmt nichts mehr an: {hinweis}[/yellow]")
            return Kommentarausgang(False, hinweis=hinweis, gruppenlimit=True)

        if hinweis := _seitenhinweis(page, AUSSTEHEND):
            console.print(
                f"[yellow]Abgeschickt, wartet aber auf Freigabe: {hinweis}[/yellow]"
            )
            return Kommentarausgang(True, hinweis=hinweis, wartet_auf_freigabe=True)

        console.print("[green]Comment submitted successfully![/green]")
        return Kommentarausgang(True)

    finally:
        page.close()


def mit_bildtexten(text: str, bildtexte: list[str | None]) -> str:
    """Der Artikeltext, ergaenzt um die Bildbeschreibungen (``alt``).

    In den Reisegruppen steht die Ankuendigung oft **im Bild**: ein Foto mit
    "نازل ع الشام 27/9 - معي وزن" darauf und kein Wort daneben. Der
    Artikeltext ist dann leer, und ``inhalt.lies`` meldete ``UNLESBAR`` -
    fuer den Beitrag, der am deutlichsten eine Gelegenheit ist. Facebook
    schreibt die erkannte Schrift in das ``alt`` des Bildes ("قد تكون صورة
    ‏نص‏ '...'"); aus der Musterliste des Nutzers vom 23.09.2026.

    **Dieselbe Grenze wie fuer den Text:** durchgereicht, nicht gespeichert.
    Die Bildbeschreibung wandert mit dem Text zu ``inhalt.lies`` und endet
    dort. Doppelte Beschriftungen fallen weg (ein Beitrag mit vier Bildern
    traegt oft viermal dieselbe), und der Anteil ist gedeckelt, damit ein
    Album den eigentlichen Text nicht verdraengt.
    """
    gesehen: list[str] = []
    for alt in bildtexte:
        alt = (alt or "").strip()
        if alt and alt not in gesehen:
            gesehen.append(alt)
    if not gesehen:
        return text
    anhang = " ".join(gesehen)[:300]
    return f"{text}\n{anhang}" if text else anhang


ARTIKEL_FRIST_MS = 3000

_ATTRIBUTE_JS = "(els, name) => els.map(e => e.getAttribute(name))"


def _adresse_nach_hover(article, group_id: str) -> list[str]:
    """Die Beitragsadresse, die Facebook erst beim Ueberfahren einsetzt.

    Die Zeitangabe eines Beitrags ist sein Verweis - aber im Strom steht
    darin oft nur ``#``; die echte Adresse setzt Facebook ein, sobald die
    Maus darueber steht. Ohne diesen Handgriff blieben solche Artikel ohne
    Adresse: "articles last seen: 5", "Found 1 post(s)". Hoechstens zwei
    Verweise, kurze Frist - ein Artikel, der nicht mitmacht, faellt aus.
    """
    from fbgroups.urls import beitragslinks

    try:
        leer = article.locator("a[href^='#']")
        for i in range(min(leer.count(), 2)):
            leer.nth(i).hover(timeout=1500)
        return beitragslinks(
            article.locator("a[href]").evaluate_all(_ATTRIBUTE_JS, "href"), group_id
        )
    except Exception:  # noqa: BLE001 - ohne Adresse faellt nur dieser Artikel aus
        return []


def _artikel_auswerten(article, group_id: str) -> dict | None:
    """Aus **einem** Artikel Adresse, Kennzahlen und der Text - oder ``None``.

    **Der Text geht durch, er bleibt nicht.** Die Grenze des Projekts
    verbietet, Beitragsinhalte zu **speichern**; gelesen wurde der Artikeltext
    hier seit jeher, um Reaktionen und Kommentarzahlen daraus zu zaehlen. Seit
    dem 12.09.2026 wird er ausserdem weitergereicht - an
    ``marketing/inhalt.py``, das daraus ein Schlagwort macht ("versand",
    "wohnung") und ein Urteil ("hoch", "keine").

    Was danach in die Datenbank geht, ist dieses Urteil. ``GroupPost`` hat
    kein Textfeld, und ``upsert_group_posts`` koennte den Text gar nicht
    speichern - das ist die Stelle, an der die Grenze technisch haelt und
    nicht nur als Vorsatz.

    Ohne diesen Schritt kommentierte der Lauf den Beitrag mit den meisten
    Reaktionen - also den lautesten, nicht den passendsten. Ein Kommentar
    ueber Paketmitnahme unter einem Wohnungsgesuch ist Spam, gleich wie gut
    er formuliert ist.
    """
    from fbgroups.textnorm import parse_member_count
    from fbgroups.urls import beitragslinks

    kandidaten = beitragslinks(
        article.locator("a[href]").evaluate_all(_ATTRIBUTE_JS, "href"),
        group_id,
    )
    if not kandidaten:
        kandidaten = _adresse_nach_hover(article, group_id)
    if not kandidaten:
        return None

    text_content = article.inner_text(timeout=ARTIKEL_FRIST_MS)
    try:
        bildtexte = article.locator("img[alt]").evaluate_all(_ATTRIBUTE_JS, "alt")
    except Exception:  # noqa: BLE001 - ohne Bildtexte bleibt der Artikeltext
        bildtexte = []

    comments_match = re.search(
        r"(\d[\d.,\s]*(?:[kKmM]|Tsd\.?|Mio\.?)?)\s*(?:Kommentare?|comments?|تعليقات|تعليق)",
        text_content,
        re.IGNORECASE,
    )
    comments_count = (parse_member_count(comments_match.group(1)) if comments_match else 0) or 0

    reactions_locator = article.locator(
        "[aria-label*='gefällt das'], [aria-label*='Reaktionen'], "
        "[aria-label*='likes'], [aria-label*='reactions'], "
        "[aria-label*='تفاعل'], [aria-label*='إعجاب']"
    ).first
    interactions_count = 0
    if reactions_locator.count() > 0:
        aria = reactions_locator.get_attribute("aria-label", timeout=ARTIKEL_FRIST_MS) or ""
        num_match = re.search(r"(\d[\d.,\s]*(?:[kKmM]|Tsd\.?|Mio\.?)?)", aria)
        if num_match:
            interactions_count = parse_member_count(num_match.group(1)) or 0

    return {
        "post_url": kandidaten[0],
        "interactions": interactions_count,
        "comments": comments_count,
        "text": mit_bildtexten(text_content[:600], bildtexte),
    }


SCROLL_RUNDEN = 15


def fetch_top_posts(
    context: BrowserContext,
    group_url: str,
    group_id: str,
    limit: int = 5,
    *,
    runden: int = SCROLL_RUNDEN,
    bekannt: Iterable[str] = (),
    geeignet: Callable[[dict], bool] | None = None,
    mindestens: int = 0,
    bericht: dict | None = None,
) -> list[dict]:
    """Scrapes recent posts from the group for metrics (NO TEXT/AUTHORS).

    **Eingesammelt wird waehrend des Scrollens, nicht danach.** Facebook
    haengt Beitraege wieder aus dem DOM, sobald sie aus dem Blick geraten -
    der Strom ist virtualisiert. Die vorige Fassung scrollte erst viermal um
    1200 Pixel und suchte dann: In einer Gruppe mit zwei Beitraegen war zu
    diesem Zeitpunkt **nichts** mehr da. Am 10.09.2026 stand deshalb "Found 0
    articles." auf dem Bildschirm, waehrend im Browser zwei Beitraege zu
    sehen waren - und das Warten auf ``div[role='article']`` hatte kurz
    zuvor noch angeschlagen.

    Deshalb: nach jedem kleinen Schritt lesen, das Gefundene behalten, und
    aufhoeren, sobald genug beisammen ist.

    **Die eine Scroll- und Suchfunktion** (23.09.2026). Das Analyseskript des
    Nutzers (``GroupPostAnalyzer.scan_and_analyze_current_group``) tat
    dasselbe - scrollen, ``div[role='article']`` lesen, Text und Bildtexte
    zusammen auswerten -, aber getrennt vom Lauf. Uebernommen ist sein Kern,
    nicht eine zweite Schleife: bis zu ``runden`` Runden (15), und **nach
    jeder Runde** wird geprueft, ob ein geeigneter Beitrag dabei ist.

    * ``bekannt`` - Beitraege, unter denen schon ein Kommentar von uns steht.
      Sie zaehlen nicht mit und kommen nicht zurueck: Bis dahin fuellten sie
      die Menge, und die Suche hoerte auf, bevor ein neuer Beitrag in Sicht
      kam ("Found 1 post(s) ... alle sichtbaren schon kommentiert").
    * ``geeignet`` - das Urteil des Laufs (Inhalt, Relevanz, Vorlage). Ist es
      gegeben, wird **nicht** bei ``limit`` aufgehoert, sondern erst, wenn
      ein Beitrag es besteht, oder nach der letzten Runde. Ein ungeeigneter
      Beitrag beendet die Suche nicht.
    * ``mindestens`` - wie viele Beitraege **mindestens** angesehen werden,
      bevor ein geeigneter die Suche beendet (24.09.2026, im Lauf 10). Bis
      dahin hoerte sie beim ersten geeigneten auf - und scheiterte der, gab
      es keinen zweiten: "Found 1 post(s) ... 1 Beitraege versucht", Runde
      fuer Runde derselbe Beitrag.
    * ``bericht`` - wird befuellt: ``runden``, ``runden_max``, ``gesehen``
      (alle Beitraege, auch die bekannten) und ``geeignet`` (ob einer das
      Urteil bestand). Daran entscheidet der Lauf, ob in einer Gruppe
      **wirklich** nichts zu machen ist.
    """
    page = context.new_page()
    gesammelt: dict[str, dict] = {}
    schon_kommentiert: set[str] = set(bekannt)
    uebersprungen: set[str] = set()
    gefunden_in = 0
    runden_max = max(int(runden), 1)
    runden = 0
    zuletzt = 0
    try:
        console.print(f"Navigating to {group_url} to fetch posts...")
        page.goto(group_url, wait_until="domcontentloaded", timeout=60000)

        try:
            page.wait_for_selector("div[role='article']", timeout=20000)
        except Exception:
            console.print(
                "[yellow]Keine Artikel im Aufbau gefunden - es wird trotzdem gesucht.[/yellow]"
            )

        while runden < runden_max:
            runden += 1
            articles = page.locator("div[role='article']").all()
            zuletzt = len(articles)
            for article in articles:
                try:
                    daten = _artikel_auswerten(article, group_id)
                except Exception as e:  # noqa: BLE001 - ein Artikel darf ausfallen
                    console.print(f"Error parsing article: {e}")
                    continue
                if daten is None:
                    continue
                if daten["post_url"] in schon_kommentiert:
                    uebersprungen.add(daten["post_url"])
                    continue
                vorher = gesammelt.get(daten["post_url"])
                if vorher is None or (vorher["interactions"] == 0 and vorher["comments"] == 0):
                    gesammelt[daten["post_url"]] = daten

            if not gesammelt:
                try:
                    from fbgroups.urls import beitragslinks

                    hrefs = page.eval_on_selector_all(
                        "a[href]", "els => els.map(e => e.getAttribute('href'))"
                    )
                    for url in beitragslinks(hrefs, group_id):
                        if url in schon_kommentiert:
                            uebersprungen.add(url)
                            continue
                        gesammelt.setdefault(
                            url,
                            {
                                "post_url": url,
                                "interactions": 0,
                                "comments": 0,
                                "text": "",
                            },
                        )
                except Exception as e:  # noqa: BLE001 - der Rueckfall darf ausfallen
                    console.print(f"Seitenweite Suche nicht moeglich: {e}")

            if geeignet is not None:
                if not gefunden_in and any(geeignet(p) for p in gesammelt.values()):
                    gefunden_in = runden
                if gefunden_in and len(gesammelt) >= mindestens:
                    break
            elif len(gesammelt) >= limit:
                break

            page.evaluate("window.scrollBy(0, 800)")
            page.wait_for_timeout(1500)

        console.print(
            f"Found {len(gesammelt)} post(s) in {runden} round(s); "
            f"articles last seen: {zuletzt}."
            + (
                f" Already commented or failed before: {len(uebersprungen)}."
                if uebersprungen
                else ""
            )
            + (
                f" Suitable post in round {gefunden_in}."
                if gefunden_in
                else (f" No suitable post in {runden} round(s)." if geeignet else "")
            )
        )

    finally:
        page.close()
        if bericht is not None:
            bericht.update(
                runden=runden,
                runden_max=runden_max,
                gesehen=len(gesammelt) + len(uebersprungen),
                geeignet=bool(gefunden_in),
            )

    posts_data = list(gesammelt.values())
    return posts_data if geeignet is not None else posts_data[:limit]
