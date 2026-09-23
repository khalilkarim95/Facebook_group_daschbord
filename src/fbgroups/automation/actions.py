import random
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
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


# Der Knopf, mit dem man die Vorschaukarte wieder loswird. Er entsteht
# **mit** der Karte und ist damit das deutlichste Zeichen, dass sie da ist.
# Drei Sprachen, weil die Oberflaeche in drei Sprachen laufen kann; der
# Vergleich ist unscharf (``*=``) und ohne Ruecksicht auf Gross- und
# Kleinschreibung (``i``), weil Facebook die Beschriftung mal mit und mal
# ohne Zusatz fuehrt ("Vorschau entfernen", "Remove post preview").
_VORSCHAU_ZEICHEN = ", ".join(
    f"{_DIALOG} [aria-label*='{wort}' i]"
    for wort in ("Vorschau", "preview", "معاينة")
)


#: Die eine Adresse im Beitrag. ``[^\s<>"\')]`` schneidet Satzzeichen ab, die
#: in der Vorlage hinter dem Link stehen duerfen - ein angehaengtes Komma
#: gehoerte sonst zur Adresse und machte den Klick zum 404.
_ADRESSE = re.compile(r'https?://[^\s<>"\'\)]+')


def trenne_adresse(text: str) -> tuple[str, str]:
    """``(Text ohne die Adresse, die Adresse)`` - oder ``(text, "")``.

    Der Rueckbau des Beitragstextes fuer den Handgriff, um den es geht: Erst
    den Link einfuegen, damit Facebook die Vorschaukarte baut, dann die nackte
    Adresse wieder herausnehmen. Die Karte bleibt stehen und bleibt anklickbar
    - und im Beitrag steht kein Aktenzeichen mehr.

    **Nur bei genau einer Adresse.** Keine heisst: nichts zu tun. Mehrere
    heissen: Wir wissen nicht, welche die Karte gebaut hat, und die falsche zu
    entfernen naehme dem Beitrag seinen Link. In beiden Faellen kommt der Text
    unveraendert zurueck, und der Aufrufer laesst es bleiben.

    Aufgeraeumt wird nur, was das Entfernen hinterlaesst: Leerzeichen am
    Zeilenende und die Zeile selbst, wenn sie nur aus der Adresse bestand. Die
    Vorlagen setzen den Link meist auf eine eigene Zeile; bliebe sie leer,
    endete jeder Beitrag mit einem Absatz ins Nichts.
    """
    treffer = _ADRESSE.findall(text)
    if len(treffer) != 1:
        return text, ""

    adresse = treffer[0]
    # **Nur am Zeilenende.** Die Vorlagen setzen den Link ans Satz- oder
    # Zeilenende ("... من هنا: {link}") oder auf eine eigene Zeile. Stuende er
    # mitten im Satz, hinterliesse das Entfernen eine Luecke darin - aus
    # "Text (siehe {link}), danke" wuerde "Text (siehe ), danke". Lieber die
    # Adresse stehen lassen als einen zerbrochenen Satz posten.
    rest = text.split(adresse, 1)[1]
    if rest.splitlines() and rest.splitlines()[0].strip():
        return text, ""
    zeilen = []
    for zeile in text.split("\n"):
        if adresse not in zeile:
            zeilen.append(zeile)
            continue
        gekuerzt = zeile.replace(adresse, "").rstrip()
        # Eine Zeile, die **durch das Entfernen** leer wurde, faellt ganz weg.
        # Eine Leerzeile, die vorher schon dastand, bleibt stehen - sie ist
        # ein Absatz und kein Rest. Ohne diesen Unterschied endete jeder
        # Beitrag, dessen Vorlage den Link auf eine eigene Zeile setzt, mit
        # einem Absatz ins Nichts.
        if gekuerzt:
            zeilen.append(gekuerzt)
    ohne = "\n".join(zeilen).rstrip()
    # Ein Text, der nur aus dem Link bestand, darf nicht als leerer Beitrag
    # hinausgehen - dann bleibt die Adresse lieber stehen.
    return (ohne, adresse) if ohne.strip() else (text, "")


def _karte_da(page, bilder_vorher: int) -> bool:
    """Ob im Dialog gerade eine Vorschaukarte steht.

    Zwei Zeichen, und beide sind Anzeichen und kein Beweis: der Knopf zum
    Entfernen der Karte, und ein Bild mehr im Dialog als vorher. Der Text des
    Entwurfs taugt nicht dafuer - die Adresse steht ja ohnehin darin.
    """
    return (
        page.locator(_VORSCHAU_ZEICHEN).count() > 0
        or page.locator(f"{_DIALOG} img").count() > bilder_vorher
    )


def _warte_auf_vorschau(page, text: str, bilder_vorher: int, frist_ms: int = 15000) -> bool:
    """Wartet, bis Facebook die Vorschaukarte zum Link im Entwurf gebaut hat.

    **Warum ueberhaupt gewartet wird.** Die Karte entsteht nicht beim
    Schreiben, sondern erst, nachdem Facebook den Link im Entwurf entdeckt,
    ihn selbst abgerufen und Bild und Titel geladen hat - das dauert einige
    Sekunden. Wer sofort absendet, veroeffentlicht die nackte Adresse. Genau
    das stand am 10.09.2026 im ersten automatisch gesetzten Beitrag:
    "https://go.b-tarikak.de/r/FB-SYR-BER-010-B" als blauer Text, ohne Bild,
    ohne Namen - es liest sich wie ein Code und nicht wie eine App.

    ``bilder_vorher`` kommt vom Aufrufer und wird **vor** dem Einfuegen
    gezaehlt: Die Karte erkennt man an einem Bild mehr, und "mehr als vorher"
    braucht ein Vorher.

    Returns: ob eine Karte erkannt wurde. Kommt keine, wird trotzdem gepostet
    - ein Beitrag ohne Karte ist besser als kein Beitrag.
    """
    if not re.search(r"https?://", text):
        return False

    for _ in range(max(frist_ms // 500, 1)):
        if _karte_da(page, bilder_vorher):
            console.print("[green]Vorschaukarte ist da.[/green]")
            # Kurz stehenlassen: Das Bild laedt noch, waehrend der Rahmen
            # schon steht.
            page.wait_for_timeout(random.randint(1200, 2000))
            return True
        page.wait_for_timeout(500)

    console.print(
        "[yellow]Keine Vorschaukarte im Entwurf - der Beitrag geht ohne sie hinaus.[/yellow]"
    )
    return False


def _ersetze_entwurf(page, textbox, text: str) -> None:
    """Tauscht den gesamten Entwurf gegen einen anderen Text.

    Alles markieren und ueberschreiben statt die Adresse einzeln
    herauszuloeschen: Wo genau sie im Feld steht, weiss nur Facebooks
    Editor - er bricht um, und ein contenteditable zaehlt Zeichen anders als
    eine Zeichenkette. Ein Markieren trifft immer.
    """
    textbox.click(delay=random.randint(80, 200))
    page.keyboard.press("Control+A")
    page.wait_for_timeout(random.randint(150, 400))
    page.keyboard.insert_text(text)


def _link_verbergen(page, textbox, text: str, bilder_vorher: int) -> tuple[bool, str]:
    """Nimmt die nackte Adresse aus dem Entwurf - wenn die Karte das ueberlebt.

    Returns: ``(Adresse verborgen, Hinweis)``.

    **Der Handgriff und seine Bedingung.** Facebook baut die Vorschaukarte aus
    dem Link im Entwurf und behaelt sie, wenn man den Link danach wieder
    herausnimmt - sie bleibt anklickbar und fuehrt weiterhin auf
    ``/r/{code}``, also wird der Klick weiterhin gezaehlt. Was verschwindet,
    ist allein die nackte Adresse im Text.

    Behaelt Facebook die Karte **nicht**, wird der volle Text wieder
    hergestellt und das ausdruecklich gemeldet. Ein Beitrag ohne Link waere
    ein Beitrag, dessen Gruppe nie einen Klick gutgeschrieben bekommt - und
    ihn trotzdem als gelungen zu verbuchen waere genau die Art stiller
    Fehlschlag, die dieses Projekt an anderer Stelle teuer bezahlt hat
    (``comment_on_post`` meldete bis zum 12.09.2026 jeden Versuch als Erfolg).
    """
    ohne, adresse = trenne_adresse(text)
    if not adresse:
        return False, "mehr als eine oder gar keine Adresse im Text"

    _ersetze_entwurf(page, textbox, ohne)
    # Facebook braucht einen Moment, um zu merken, dass der Link weg ist -
    # sofort nachzusehen hiesse, die alte Karte zu sehen und zufrieden zu sein.
    page.wait_for_timeout(random.randint(1500, 2500))

    if _karte_da(page, bilder_vorher):
        console.print("[green]Adresse entfernt, die Vorschaukarte bleibt.[/green]")
        return True, ""

    console.print(
        "[yellow]Ohne die Adresse verschwindet die Vorschaukarte - "
        "der Link bleibt im Text stehen.[/yellow]"
    )
    _ersetze_entwurf(page, textbox, text)
    _warte_auf_vorschau(page, text, bilder_vorher)
    return False, "Vorschaukarte haelt ohne die Adresse nicht - Link sichtbar im Beitrag"


@dataclass(frozen=True)
class Beitragsausgang:
    """Was aus einem abgesetzten Beitrag geworden ist.

    Wie ``Kommentarausgang`` und aus demselben Grund: Ein ``bool`` beantwortet
    "ging es?" und verschweigt "wie sieht es aus?". Seit der Beitrag die nackte
    Adresse verbergen soll, ist das zweite eine eigene Frage - und eine, die
    ein Mensch spaeter stellt, wenn er den Beitrag in der Gruppe sieht.
    """

    erfolg: bool
    hinweis: str = ""
    #: Ob Facebook eine Vorschaukarte gebaut hat.
    karte: bool = False
    #: Ob die nackte Adresse im veroeffentlichten Text steht. ``True`` ist
    #: kein Fehlschlag - der Beitrag steht, und sein Link wird gezaehlt -,
    #: aber es ist das, was eigentlich vermieden werden sollte.
    link_sichtbar: bool = True


def post_to_group(
    context: BrowserContext,
    group_url: str,
    text: str,
    *,
    link_verbergen: bool = True,
) -> Beitragsausgang:
    """Setzt den eigenen Beitrag in der Gruppe ab.

    **Die Adresse baut die Karte und geht dann wieder aus dem Text.** Der
    Ablauf ist der Handgriff, den ein Mensch macht: Link einfuegen, warten,
    bis Facebook die Vorschaukarte gebaut hat, die nackte Adresse
    herausnehmen, absenden. Die Karte bleibt anklickbar und fuehrt weiterhin
    auf ``/r/{code}`` - der Klick wird also weiterhin dieser Gruppe
    gutgeschrieben -, aber im Beitrag steht kein Aktenzeichen mehr.

    Haelt die Karte das nicht aus, wird der volle Text wiederhergestellt und
    gepostet. Das ist kein Fehlschlag, aber auch kein stiller Erfolg:
    ``link_sichtbar`` und ``hinweis`` sagen es, und der Aufrufer schreibt es
    ins Protokoll. Ein Beitrag ohne Link waere schlimmer als einer mit einer
    sichtbaren Adresse - seine Gruppe bekaeme nie einen Klick gutgeschrieben.

    ``link_verbergen=False`` laesst den Text unangetastet. Der Ausweg fuer den
    Fall, dass Facebook den Handgriff einmal nicht mehr mitmacht - eine Zahl
    in der Konfiguration statt einer Codeaenderung, wie ueberall hier.
    """
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
            # Die Frage aus der Meldung selbst beantworten, statt sie zu
            # stellen: "Bist du angemeldet?" stand hier seit jeher im
            # Klartext - und blieb folgenlos, weil der Ausgang derselbe war
            # wie bei einer gesperrten Gruppe. Jetzt entscheidet sie, ob die
            # Gruppe einen Vermerk bekommt oder der Lauf anhaelt.
            if hinweis := _seitenhinweis(page, ANMELDEWAND):
                console.print(f"[red]Nicht bei Facebook angemeldet: {hinweis}[/red]")
                return Beitragsausgang(
                    erfolg=False, hinweis=f"{NICHT_ANGEMELDET}: {hinweis}"
                )
            console.print(
                "[red]Could not find the 'Write something' button. "
                "Are you a member of the group?[/red]"
            )
            return Beitragsausgang(
                erfolg=False, hinweis="Beitragsformular nicht gefunden oder blockiert"
            )

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
            return Beitragsausgang(erfolg=False, hinweis="Kein Textfeld im Beitragsdialog")

        karte = False
        verborgen = False
        hinweis = ""
        try:
            page.wait_for_timeout(random.randint(500, 1500))
            textbox.click(delay=random.randint(100, 300))
            page.wait_for_timeout(random.randint(500, 1000))
            # Vor dem Einfuegen zaehlen: Die Karte erkennt man an einem Bild
            # mehr im Dialog, und "mehr" braucht ein Vorher.
            bilder_vorher = page.locator(f"{_DIALOG} img").count()
            # Simulate human typing
            console.print("Typing message (pasting/inserting directly)...")
            page.keyboard.insert_text(text)
            # Erst die Vorschaukarte abwarten, dann absenden - sonst steht im
            # Beitrag nur die nackte Adresse.
            karte = _warte_auf_vorschau(page, text, bilder_vorher)
            # Und dann die Adresse wieder heraus. Nur mit Karte: Ohne sie
            # naehme das Entfernen dem Beitrag seinen Link ersatzlos.
            if karte and link_verbergen:
                verborgen, hinweis = _link_verbergen(page, textbox, text, bilder_vorher)
        except PlaywrightTimeoutError:
            console.print("[red]Textfeld gefunden, aber nicht beschreibbar.[/red]")
            return Beitragsausgang(erfolg=False, hinweis="Textfeld nicht beschreibbar")

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
            return Beitragsausgang(
                erfolg=False, hinweis="Absendeknopf nicht gefunden", karte=karte,
                link_sichtbar=not verborgen,
            )

        # Wait for the posting to complete (the modal usually closes)
        page.wait_for_timeout(random.randint(4000, 6000))
        console.print("[green]Post submitted successfully![/green]")
        return Beitragsausgang(
            erfolg=True,
            hinweis=hinweis,
            karte=karte,
            link_sichtbar=not verborgen,
        )

    finally:
        page.close()


#: Was Facebook zeigt, wenn die **Gruppe** nichts mehr annimmt - nicht das
#: Konto. Am 12.09.2026 zum ersten Mal gesehen: "Du hast das Limit fuer
#: freizugebende Inhalte in dieser Gruppe erreicht."
#:
#: Es ist weder eine Ablehnung (niemand hat den Text beurteilt) noch eine
#: Sperre des Kontos (andere Gruppen gehen weiter) noch eine Bremse wegen
#: Geschwindigkeit. Es heisst: In dieser Gruppe warten schon genug Beitraege
#: von uns auf die Freigabe eines Moderators.
GRUPPENLIMIT = (
    "limit fuer freizugebende", "limit für freizugebende",
    "limit fuer inhalte", "limit für inhalte",
    "freizugebende inhalte",
    "limit for content to be approved", "limit of pending",
    "pending posts limit", "too many pending",
    "وصلت إلى الحد", "وصلت الى الحد", "الحد الأقصى للمحتوى", "بانتظار الموافقة",
)

#: Woran ein abgeschickter, aber noch nicht sichtbarer Beitrag zu erkennen
#: ist. Er steht in der Gruppe erst, wenn ein Mensch ihn freigibt - bis dahin
#: sieht ihn niemand ausser uns.
AUSSTEHEND = (
    "ausstehend", "wartet auf genehmigung", "wird ueberprueft", "wird überprüft",
    "pending", "awaiting approval", "being reviewed",
    "قيد المراجعة", "بانتظار الموافقة", "في انتظار",
)


#: Woran ein Beitrag zu erkennen ist, den es nicht mehr gibt - geloescht,
#: nur noch fuer wenige sichtbar oder von der Gruppe entfernt.
#:
#: **Der Unterschied zu allem anderen hier ist der Gegenstand.** Diese Meldung
#: sagt nichts ueber die Gruppe, nichts ueber unseren Text und nichts ueber
#: das Konto - sie sagt, dass diese eine Adresse ins Leere zeigt. Ohne die
#: Erkennung endete das als "Kommentarfeld nicht gefunden", also als Aussage
#: ueber die Gruppe; im Betrieb hat der Lauf dieselbe tote Adresse Dutzende
#: Male angesteuert und am Ende die Gruppe dafuer bezahlt.
BEITRAG_WEG = (
    "هذا المحتوى غير متوفر", "المحتوى غير متوفر", "هذا المحتوى غير متاح",
    "content isn't available", "content is no longer available",
    "this content isn't available right now",
    "inhalt ist derzeit nicht verfuegbar", "inhalt ist derzeit nicht verfügbar",
    "dieser inhalt ist nicht verfuegbar", "dieser inhalt ist nicht verfügbar",
    "seite nicht gefunden", "page not found",
)


#: Woran die **Anmeldewand** zu erkennen ist - Facebook zeigt sie jeder
#: Sitzung, die nicht angemeldet ist.
#:
#: **Sie sagt nichts ueber die Gruppe, sondern alles ueber uns**, und genau
#: darin lag die Gefahr: Ohne diese Erkennung endete sie als "Kommentarfeld
#: nicht gefunden" bzw. "Beitragsformular nicht gefunden" - also als
#: technischer Fehlschlag, und der nimmt seit dem 20.09.2026 die Gruppe aus
#: der Kampagne. Ein abgemeldeter Browser haette damit eine Kampagne nach der
#: anderen leergeraeumt, Gruppe fuer Gruppe, ohne dass an einer einzigen
#: etwas gewesen waere.
#:
#: Der Text beginnt mit "nicht angemeldet", und das ist kein Schmuck:
#: ``automatik.ist_sitzungsfehler`` erkennt ihn daran und haelt den Lauf
#: **sofort** an - dieselbe Behandlung wie bei einem geschlossenen
#: Browserfenster. Dort hilft keine naechste Gruppe.
ANMELDEWAND = (
    "log in to facebook", "log into facebook", "you must log in",
    "bei facebook anmelden", "in facebook einloggen", "du musst dich anmelden",
    "passwort vergessen", "forgot password", "forgotten password",
    "create new account", "neues konto erstellen",
    "تسجيل الدخول إلى فيسبوك", "تسجيل الدخول الى فيسبوك",
    "إنشاء حساب جديد", "نسيت كلمة السر", "نسيت كلمة المرور",
)

#: Der Vorspann jeder Meldung ueber eine Anmeldewand. Er ist die Schnittstelle
#: zu ``automatik._SITZUNG`` - wer ihn aendert, muss dort nachsehen.
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
    #: Den Beitrag gibt es nicht mehr. Kein Urteil ueber die Gruppe und kein
    #: technischer Fehlschlag - die Adresse zeigt ins Leere, der naechste
    #: Beitrag derselben Gruppe kann gehen.
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
            # Der Satz um die Fundstelle herum, damit im Protokoll steht, was
            # dort wirklich stand - und nicht nur, dass etwas passte.
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


def comment_on_post(context: BrowserContext, post_url: str, text: str) -> Kommentarausgang:
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

        # **Noch davor: Sind wir ueberhaupt angemeldet?** Die Anmeldewand
        # steht vor jeder Adresse und sagt nichts ueber diese eine aus. Als
        # "kein Kommentarfeld" gelesen waere sie ein technischer Fehlschlag,
        # und der nimmt die Gruppe aus der Kampagne - fuer etwas, das an uns
        # liegt und in der naechsten Gruppe genauso waere.
        if hinweis := _seitenhinweis(page, ANMELDEWAND):
            console.print(f"[red]Nicht bei Facebook angemeldet: {hinweis}[/red]")
            return Kommentarausgang(False, hinweis=f"{NICHT_ANGEMELDET}: {hinweis}")

        # **Zuerst: Gibt es den Beitrag ueberhaupt noch?** Sonst endet ein
        # geloeschter Beitrag als "Kommentarfeld nicht gefunden" - eine
        # Aussage ueber die Gruppe, wo eine ueber die Adresse hingehoert.
        if hinweis := _seitenhinweis(page, BEITRAG_WEG):
            console.print(f"[yellow]Diesen Beitrag gibt es nicht mehr: {hinweis}[/yellow]")
            return Kommentarausgang(False, hinweis=hinweis, beitrag_weg=True)

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
            # Vor dem Urteil "kein Feld" nachsehen, ob die Gruppe es sagt:
            # Wo das Limit fuer freizugebende Inhalte erreicht ist, blendet
            # Facebook das Feld aus. "Nicht gefunden" waere dann eine Aussage
            # ueber unsere Suche statt ueber die Gruppe.
            if hinweis := _seitenhinweis(page, GRUPPENLIMIT):
                console.print(f"[yellow]Die Gruppe nimmt gerade nichts mehr an: {hinweis}[/yellow]")
                return Kommentarausgang(False, hinweis=hinweis, gruppenlimit=True)
            console.print(
                "[red]Could not find the comment box. Are comments allowed on this post?[/red] "
                f"(Textfelder: {page.locator(_TEXTFELD).count()})"
            )
            return Kommentarausgang(False, hinweis="Kommentarfeld nicht gefunden")

        try:
            page.wait_for_timeout(random.randint(500, 1500))
            comment_box.click(delay=random.randint(100, 300))
            page.wait_for_timeout(random.randint(500, 1000))
            console.print("Typing comment (pasting/inserting directly)...")
            page.keyboard.insert_text(text)
        except PlaywrightTimeoutError:
            console.print("[red]Kommentarfeld gefunden, aber nicht beschreibbar.[/red]")
            return Kommentarausgang(False, hinweis="Kommentarfeld nicht beschreibbar")

        page.wait_for_timeout(random.randint(800, 2000))
        console.print("Submitting comment (pressing Enter)...")
        # Submitting comments on Facebook is usually just hitting Enter
        comment_box.press("Enter", delay=random.randint(50, 150))

        page.wait_for_timeout(random.randint(3000, 5000))

        # **Erst jetzt entscheidet sich, was daraus geworden ist.**
        if hinweis := _seitenhinweis(page, GRUPPENLIMIT):
            console.print(f"[yellow]Die Gruppe nimmt nichts mehr an: {hinweis}[/yellow]")
            return Kommentarausgang(False, hinweis=hinweis, gruppenlimit=True)

        if hinweis := _seitenhinweis(page, AUSSTEHEND):
            # Abgeschickt ist er - sichtbar ist er nicht. Als Erfolg gezaehlt,
            # weil der Tracking-Link heraus ist und die Fassung verbraucht;
            # aber mit Ansage, denn bis zur Freigabe klickt ihn niemand.
            console.print(
                f"[yellow]Abgeschickt, wartet aber auf Freigabe: {hinweis}[/yellow]"
            )
            return Kommentarausgang(True, hinweis=hinweis, wartet_auf_freigabe=True)

        console.print("[green]Comment submitted successfully![/green]")
        return Kommentarausgang(True)

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


#: Wie lange ein einzelner Artikel auf sich warten lassen darf, in ms.
#:
#: Playwright wartet sonst 30 Sekunden auf ein Element - und Facebooks
#: Beitragsstrom haengt Artikel beim Scrollen wieder aus. Im Log vom
#: 23.09.2026 stand deshalb mehrmals "Error parsing article: Locator.
#: get_attribute: Timeout 30000ms exceeded": eine halbe Minute je Artikel,
#: der gar nicht mehr da war.
ARTIKEL_FRIST_MS = 3000

#: Alle Werte eines Attributs **auf einmal** - ohne Warten je Element.
_ATTRIBUTE_JS = "(els, name) => els.map(e => e.getAttribute(name))"


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

    # **Die Adressen in einem Zug** (23.09.2026). Vorher wurde jeder Link
    # einzeln gefragt (``get_attribute`` je Element) - und war der Artikel
    # beim Scrollen schon wieder ausgehaengt, wartete jeder Aufruf 30
    # Sekunden auf ein Element, das nicht mehr kam. ``evaluate_all`` liest,
    # was gerade da ist, und wartet auf nichts.
    kandidaten = beitragslinks(
        article.locator("a[href]").evaluate_all(_ATTRIBUTE_JS, "href"),
        group_id,
    )
    if not kandidaten:
        return None

    text_content = article.inner_text(timeout=ARTIKEL_FRIST_MS)
    # **Die Bildtexte - und diesmal wirklich** (23.09.2026). Seit dem Morgen
    # desselben Tages stand hier ``article.eval_on_selector_all(...)``; einen
    # ``Locator`` hat diese Methode aber nicht (nur Seite und ElementHandle).
    # Der Fehler verschwand im ``except`` - gelesen wurde nie ein Bildtext,
    # und niemand merkte es, weil "keine Bildtexte" genau so aussieht.
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
        # Durchgereicht, nicht gespeichert - siehe Docstring. Gekuerzt, weil
        # fuer die Themenerkennung der Anfang genuegt und ein ganzer
        # Kommentarbaum nur Rauschen mitbraechte. Die Bildtexte kommen nach
        # dem Schnitt dazu, sonst fielen sie bei einem langen Text weg.
        "text": mit_bildtexten(text_content[:600], bildtexte),
    }


#: Wie viele Scroll-Runden eine Gruppe hoechstens bekommt (23.09.2026: 15,
#: vorher fest 5). Entspricht ``GROUP_SCROLL_ROUNDS`` aus dem Analyseskript
#: des Nutzers; im Lauf kommt die Zahl aus ``automatik.scroll_runden``.
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

        # Auf die Beitraege warten, statt eine feste Zeit zu raten: Ein
        # langsamer Aufbau sah bisher aus wie eine leere Gruppe.
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
                # Der reichere Fund gewinnt: Ein Eintrag ohne Kennzahlen
                # stammt vom Rueckfallweg und darf von einem Artikel mit
                # Zahlen abgeloest werden. Die Reihenfolge bleibt dabei, weil
                # ein vorhandener Schluessel seine Stelle behaelt.
                if vorher is None or (vorher["interactions"] == 0 and vorher["comments"] == 0):
                    gesammelt[daten["post_url"]] = daten

            if not gesammelt:
                # **Rueckfallweg: die ganze Seite statt der einzelnen Artikel.**
                # Die Adresse eines Beitrags steht auf der Seite auch dann,
                # wenn kein Verweis innerhalb des Rahmens liegt, den
                # ``div[role='article']`` aufspannt. Kennzahlen gibt es hier
                # nicht: Ohne den Artikel ist nicht zu sagen, welche
                # Reaktionen zu welchem Beitrag gehoeren - und eine geratene
                # Zahl waere schlimmer als keine, denn nach ihr wird der
                # beste Beitrag ausgewaehlt.
                try:
                    from fbgroups.urls import beitragslinks

                    hrefs = page.eval_on_selector_all(
                        "a[href]", "els => els.map(e => e.getAttribute('href'))"
                    )
                    for url in beitragslinks(hrefs, group_id):
                        if url in schon_kommentiert:
                            uebersprungen.add(url)
                            continue
                        # Ohne Artikel gibt es weder Kennzahlen noch Text -
                        # und eine geratene Zahl waere schlimmer als keine.
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
                # **Nach jeder Runde das Urteil** - sobald ein Beitrag es
                # besteht, wird kommentiert statt weiter gescrollt.
                if any(geeignet(p) for p in gesammelt.values()):
                    gefunden_in = runden
                    break
            elif len(gesammelt) >= limit:
                break

            # Kleine Schritte: Wer in einer Gruppe mit drei Beitraegen 4800
            # Pixel weit scrollt, steht hinter dem Ende des Stroms - und dort
            # ist nichts mehr eingehaengt.
            page.evaluate("window.scrollBy(0, 800)")
            page.wait_for_timeout(1500)

        console.print(
            f"Found {len(gesammelt)} post(s) in {runden} round(s); "
            f"articles last seen: {zuletzt}."
            + (f" Already commented: {len(uebersprungen)}." if uebersprungen else "")
            + (
                f" Suitable post in round {gefunden_in}."
                if gefunden_in
                else (f" No suitable post in {runden} round(s)." if geeignet else "")
            )
        )

    finally:
        page.close()

    posts_data = list(gesammelt.values())
    return posts_data if geeignet is not None else posts_data[:limit]
