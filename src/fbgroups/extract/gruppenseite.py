"""Liest die oeffentlich erreichbare Gruppenseite auf facebook.com.

**Dieses Modul hebt eine harte Projektgrenze auf.** Bis zum 27.08.2026 galt
"kein Zugriff auf facebook.com" ausnahmslos; der Nutzer hat die Grenze an
diesem Tag ausdruecklich fuer das Lesen oeffentlicher Gruppenseiten geoeffnet,
weil Mitgliederzahl und Aktivitaet zusammen die Haelfte des Scores tragen und
nirgends sonst zu bekommen sind. Nachgewiesen: In 147 gespeicherten
Serper-Antworten (815.630 Zeichen) kommt keine einzige Mitgliederzahl vor, in
keiner Sprache und in keiner Schreibweise.

**Was hier trotzdem nicht passiert**, und zwar nicht aus Bequemlichkeit:

* **Kein Login und keine Sitzungsuebernahme.** Abgerufen wird ohne Cookies,
  ohne Anmeldedaten, ohne uebernommene Browsersitzung. Eine angemeldete
  Abfrage waere die eine, die das Konto des Nutzers wirklich gefaehrdet - und
  an diesem Konto haengen alle Gruppenmitgliedschaften.
* **Keine Umgehung von Sperren.** Kein Proxywechsel, keine wechselnden
  Kennungen, kein Nachahmen eines Browsers, um Erkennung zu entgehen. Wer
  blockt, bekommt ``None`` und keinen Trick. ``_ABBRUCH_NACH`` beendet den
  Lauf nach mehreren Blockaden hintereinander, statt haerter zu klopfen: Das
  ist der Schutz des Kontos, nicht sein Gegenteil.
* **Keine Personendaten.** Gelesen werden Mitgliederzahl, Sichtbarkeit, Name
  und Beitragszeitpunkte der **Gruppe**. Mitglieder- und Adminnamen,
  Profil-URLs, Beitragsinhalte und Kontaktdaten werden nicht gelesen und
  koennten auch nicht gespeichert werden - ``models.Group`` hat dafuer keine
  Felder, und diese Grenze steht unveraendert.
* **Kein Raten.** Was nicht ausdruecklich auf der Seite steht, bleibt
  ``None``. Eine geschaetzte Mitgliederzahl waere der gefaehrlichste Fehler
  des ganzen Projekts: Sie saehe in der Datenbank aus wie eine gemessene und
  entschiede darueber, wo die naechsten dreihundert Beitraege hingehen.

**Es wird mit Vorsatz wenig abgerufen.** Facebook liefert einem nicht
angemeldeten Abruf haeufig eine Anmeldeseite statt der Gruppe; das ist der
Normalfall und kein Fehler. Der Befund lautet dann "nicht erreichbar", die
Zahlen bleiben leer, und ``checked_at`` wird trotzdem gesetzt - sonst liefe
derselbe erfolglose Abruf bei jedem Lauf erneut.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from fbgroups.config import AppConfig
from fbgroups.extract.aktivitaet import parse_relative_datum
from fbgroups.importers.manual_seed import parse_member_count
from fbgroups.models import PrivacyHint
from fbgroups.utils.rate_limit import RateLimiter

#: Nach so vielen Blockaden hintereinander endet der Lauf. Weiterzuklopfen
#: waere genau das Verhalten, das eine Sperre ausloest - und die Sperre traefe
#: das Konto, an dem alle Gruppenmitgliedschaften haengen.
_ABBRUCH_NACH = 5

#: Ehrliche Kennung. Kein nachgeahmter Browser: Wer uns aussperren will, soll
#: uns erkennen koennen. Das ist der Unterschied zwischen Abrufen und
#: Erkennung-Umgehen, und er ist der Grund, warum dieses Modul ueberhaupt
#: vertretbar ist.
_KENNUNG = "fbgroups/1.0 (Gruppenrecherche; nur oeffentliche Seiten; kein Login)"

_MITGLIEDER = [
    re.compile(
        r"([\d., \s]{1,15}(?:\s*[kmKM])?)\s*"
        r"(?:mitglieder|members|عضو|أعضاء)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:mitglieder|members)\s*[:·-]?\s*([\d., \s]{1,15}(?:\s*[kmKM])?)",
        re.IGNORECASE,
    ),
]

_OEFFENTLICH = ("öffentliche gruppe", "public group", "مجموعة عامة")
_PRIVAT = ("private gruppe", "private group", "geschlossene gruppe", "مجموعة خاصة")

#: Zeichen dafuer, dass statt der Gruppe eine Anmeldeseite kam. Dann ist der
#: Abruf nicht fehlgeschlagen - er wurde beantwortet, nur nicht mit Inhalt.
_ANMELDEWAND = (
    "you must log in to continue",
    "du musst dich anmelden",
    "log into facebook",
    "bei facebook anmelden",
    "melde dich an oder registriere dich",
    'name="login_form"',
)

_META = re.compile(
    r'<meta[^>]+property=["\']og:(title|description)["\'][^>]+content=["\']([^"\']*)["\']',
    re.IGNORECASE,
)

_CACHE_SCHEMA = """
CREATE TABLE IF NOT EXISTS gruppenseiten (
    group_id     TEXT PRIMARY KEY,
    erreichbar   INTEGER NOT NULL DEFAULT 0,
    member_count INTEGER,
    privacy      TEXT,
    name         TEXT,
    -- Wie viele datierte Beitraege gefunden wurden und wann der juengste war.
    -- Der Rohtext wird NICHT gespeichert: Beitragsinhalte gehoeren nicht in
    -- diesen Bestand, und die harte Grenze dafuer steht unveraendert.
    beitrag_daten INTEGER NOT NULL DEFAULT 0,
    juengster_beitrag TEXT,
    status_code  INTEGER,
    abgerufen_am TEXT NOT NULL
);
"""


@dataclass
class Seitenbefund:
    """Was von einer Gruppenseite abzulesen war - und was nicht.

    Jedes Feld darf ``None`` sein, und das ist keine Nachlaessigkeit: Eine
    Anmeldewand liefert einen gueltigen Befund mit lauter leeren Feldern.
    ``erreichbar`` unterscheidet "wir haben nachgesehen und nichts gefunden"
    von "wir haben nicht nachgesehen".
    """

    group_id: str
    erreichbar: bool = False
    member_count: int | None = None
    privacy: PrivacyHint = PrivacyHint.UNKNOWN
    name: str | None = None
    beitrag_daten: list[datetime] = field(default_factory=list)
    status_code: int | None = None
    abgerufen_am: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def juengster_beitrag(self) -> datetime | None:
        return max(self.beitrag_daten) if self.beitrag_daten else None


class Blockiert(RuntimeError):
    """Facebook hat den Lauf abgewiesen - er endet, statt haerter zu klopfen."""


def lies_seite(html: str, group_id: str, *, status_code: int | None = None) -> Seitenbefund:
    """Wertet den HTML-Text einer Gruppenseite aus. Rein, ohne Netz.

    Getrennt vom Abruf, damit sie ohne facebook.com pruefbar ist: Die
    Auswertung ist die Stelle, an der eine erfundene Zahl entstehen wuerde,
    und genau die muss ein Test festhalten koennen.
    """
    befund = Seitenbefund(group_id=group_id, status_code=status_code)
    klein = html.lower()

    if any(marker in klein for marker in _ANMELDEWAND):
        # Beantwortet, aber ohne Inhalt. Kein Fehler und kein Grund,
        # es gleich noch einmal zu versuchen.
        return befund

    befund.erreichbar = True

    # og:title und og:description tragen bei Facebook die verlaesslichsten
    # Angaben - sie stehen im Quelltext und nicht erst nach dem Skriptlauf.
    metadaten = {art.lower(): inhalt for art, inhalt in _META.findall(html)}
    if metadaten.get("title"):
        befund.name = metadaten["title"].strip() or None

    durchsuchen = " ".join([metadaten.get("description", ""), metadaten.get("title", ""), html])
    for muster in _MITGLIEDER:
        treffer = muster.search(durchsuchen)
        if treffer is None:
            continue
        zahl = parse_member_count(treffer.group(1).strip())
        # 0 ist keine Mitgliederzahl, sondern ein Auswertungsfehler: Eine
        # Gruppe mit null Mitgliedern gibt es nicht.
        if zahl:
            befund.member_count = zahl
            break

    if any(marker in klein for marker in _OEFFENTLICH):
        befund.privacy = PrivacyHint.PUBLIC
    elif any(marker in klein for marker in _PRIVAT):
        befund.privacy = PrivacyHint.PRIVATE

    # Beitragszeitpunkte: nur die Zeitangaben, nie der Beitragstext.
    # {0,40} und nicht {1,40}: Steht "vor 2 Stunden" allein im Element, gibt
    # es kein Zeichen davor - mit einem Mindestabstand fiele genau der
    # haeufigste Fall durch.
    for text in set(re.findall(r">([^<>]{0,40}vor \d+ *\w+[^<>]{0,20})<", html)):
        zeitpunkt = parse_relative_datum(text)
        if zeitpunkt is not None:
            befund.beitrag_daten.append(zeitpunkt)

    return befund


class Gruppenseiten:
    """Abruf mit Zwischenspeicher, Mindestabstand und Notbremse.

    Der Zwischenspeicher steht in einer eigenen Datei - wie beim
    Anfragespeicher der Suche und aus demselben Grund: Ein zweiter Lauf soll
    nichts kosten, hier keine Anfrage an facebook.com. Der Mindestabstand
    stammt aus ``enrich.mindestabstand_sekunden`` und ist bewusst gross.
    """

    def __init__(self, cache_pfad: Path, config: AppConfig) -> None:
        self.config = config
        self.conn = sqlite3.connect(cache_pfad)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_CACHE_SCHEMA)
        self.conn.commit()
        abstand = float(config.get("enrich", "mindestabstand_sekunden", default=6.0) or 0.0)
        self.limiter = RateLimiter(abstand)
        self._blockaden = 0

    def __enter__(self) -> Gruppenseiten:
        return self

    def __exit__(self, *_: object) -> None:
        self.conn.close()

    # -- Zwischenspeicher ------------------------------------------------
    def gespeichert(self, group_id: str) -> Seitenbefund | None:
        row = self.conn.execute(
            "SELECT * FROM gruppenseiten WHERE group_id = ?", (group_id,)
        ).fetchone()
        if row is None:
            return None
        juengster = row["juengster_beitrag"]
        return Seitenbefund(
            group_id=group_id,
            erreichbar=bool(row["erreichbar"]),
            member_count=row["member_count"],
            privacy=PrivacyHint(row["privacy"] or PrivacyHint.UNKNOWN.value),
            name=row["name"],
            beitrag_daten=[datetime.fromisoformat(juengster)] if juengster else [],
            status_code=row["status_code"],
            abgerufen_am=datetime.fromisoformat(row["abgerufen_am"]),
        )

    def merke(self, befund: Seitenbefund) -> None:
        juengster = befund.juengster_beitrag
        self.conn.execute(
            "INSERT INTO gruppenseiten (group_id, erreichbar, member_count, privacy, "
            "name, beitrag_daten, juengster_beitrag, status_code, abgerufen_am) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(group_id) DO UPDATE SET "
            "erreichbar=excluded.erreichbar, member_count=excluded.member_count, "
            "privacy=excluded.privacy, name=excluded.name, "
            "beitrag_daten=excluded.beitrag_daten, "
            "juengster_beitrag=excluded.juengster_beitrag, "
            "status_code=excluded.status_code, abgerufen_am=excluded.abgerufen_am",
            (
                befund.group_id,
                int(befund.erreichbar),
                befund.member_count,
                befund.privacy.value,
                befund.name,
                len(befund.beitrag_daten),
                juengster.isoformat() if juengster else None,
                befund.status_code,
                befund.abgerufen_am.isoformat(),
            ),
        )
        self.conn.commit()

    # -- Abruf ------------------------------------------------------------
    def hole(self, group_id: str, client: httpx.Client) -> Seitenbefund:
        """Ruft **eine** Gruppenseite ab. Wirft ``Blockiert``, wenn Schluss ist."""
        self.limiter.wait()
        adresse = f"https://www.facebook.com/groups/{group_id}/"
        try:
            antwort = client.get(adresse, headers={"User-Agent": _KENNUNG})
        except httpx.HTTPError:
            # Netzfehler ist kein Befund: nicht merken, nicht als geprueft
            # zaehlen. Sonst stuende eine Gruppe dauerhaft als "nichts
            # gefunden" da, weil einmal das WLAN weg war.
            return Seitenbefund(group_id=group_id, erreichbar=False, status_code=None)

        if antwort.status_code in (401, 403, 429) or antwort.status_code >= 500:
            self._blockaden += 1
            if self._blockaden >= _ABBRUCH_NACH:
                raise Blockiert(
                    f"{self._blockaden} Abweisungen hintereinander "
                    f"(zuletzt HTTP {antwort.status_code}) - Lauf beendet. "
                    "Haerter zu klopfen waere genau das, was eine Sperre ausloest."
                )
            return Seitenbefund(
                group_id=group_id, erreichbar=False, status_code=antwort.status_code
            )

        self._blockaden = 0
        befund = lies_seite(antwort.text, group_id, status_code=antwort.status_code)
        self.merke(befund)
        return befund
