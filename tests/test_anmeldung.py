"""Die Anmeldewand ist kein Urteil ueber die Gruppe.

Der Anlass ist eine Frage des Nutzers nach einem Lauf, der nichts tat:
*"muss eben Facebook-Anmeldung nicht sein, sodass die Sitzung vor
Automatik-Beginn gespeichert ist?"* - und beim Nachsehen stand dahinter eine
Luecke, die teurer ist als der Lauf, der sie aufgedeckt hat.

Eine abgemeldete Browsersitzung findet in **jeder** Gruppe kein
Kommentarfeld und kein Beitragsformular. Beides endete als "nicht gefunden",
also als technischer Fehlschlag - und ein technischer Fehlschlag nimmt seit
dem 20.09.2026 die Gruppe aus der Kampagne (``bearbeiten = 0``). Ein
abgelaufener Anmeldestand haette damit eine Kampagne nach der anderen
leergeraeumt, mit einem Grund an jeder Gruppe, an dem nichts liegt.

Drei Riegel, und jeder hat hier seinen Test:

1. Der Lauf faengt ohne Anmeldung gar nicht erst an (``_sitzung_pruefen``).
2. Eine Anmeldewand mitten im Lauf ist ein **Sitzungsfehler** - der Lauf
   haelt an, statt weiterzumachen.
3. Wegen eines Sitzungsfehlers wird **keine** Gruppe ausgeschlossen.
"""

from __future__ import annotations

from pathlib import Path

from fbgroups.automation import actions
from fbgroups.marketing import automatik

ARABISCHE_WAND = (
    "تسجيل الدخول إلى فيسبوك\nالبريد الإلكتروني أو رقم الهاتف\n"
    "نسيت كلمة السر؟\nإنشاء حساب جديد"
)
DEUTSCHE_WAND = "Bei Facebook anmelden\nPasswort vergessen?\nNeues Konto erstellen"


class _Seite:
    """Eine Seite, die nur ihren Text kennt - mehr fragt ``_seitenhinweis`` nicht."""

    def __init__(self, text: str) -> None:
        self._text = text

    def inner_text(self, _was: str) -> str:
        return self._text


def _wand(text: str) -> str:
    return actions._seitenhinweis(_Seite(text), actions.ANMELDEWAND)


# --- 1. Erkannt wird sie in jeder Sprache --------------------------------

def test_die_anmeldewand_wird_arabisch_erkannt() -> None:
    """Der Regelfall dieses Projekts: ein arabisch eingestelltes Konto."""
    assert _wand(ARABISCHE_WAND)


def test_die_anmeldewand_wird_deutsch_und_englisch_erkannt() -> None:
    assert _wand(DEUTSCHE_WAND)
    assert _wand("You must log in to continue.\nLog in to Facebook")


def test_eine_gewoehnliche_gruppenseite_ist_keine_anmeldewand() -> None:
    """Sonst hielte der Lauf mitten in der Arbeit an - und nichts waere los.

    Die Muster stehen bewusst dort, wo sie **nur** abgemeldet vorkommen:
    "Neues Konto erstellen" und "Passwort vergessen" sieht ein angemeldetes
    Konto nie.
    """
    assert not _wand(
        "مسافر من المانيا الى سوريا\nشو الأخبار؟\nاكتب شيئًا...\nتعليق\nإعجاب\nمشاركة"
    )
    assert not _wand("Schreib etwas...\nKommentieren\nGefaellt mir\nTeilen\n25 Beitraege")


# --- 2. Sie ist ein Sitzungsfehler, kein technischer Fehlschlag ----------

def test_die_anmeldewand_haelt_den_lauf_an() -> None:
    """Hier hilft keine naechste Gruppe - der einzige Grund anzuhalten.

    Die Bruecke ist der Vorspann ``NICHT_ANGEMELDET``: ``ist_sitzungsfehler``
    erkennt ihn. Wer ihn aendert, muss ``automatik._SITZUNG`` nachziehen -
    deshalb steht er als Konstante da und nicht als Satz im Code.
    """
    fehler = f"{actions.NICHT_ANGEMELDET}: {_wand(ARABISCHE_WAND)}"

    assert automatik.ist_sitzungsfehler(fehler)
    waechter = automatik._Technikwaechter()
    assert waechter.melde(automatik.Schrittergebnis(erfolg=False, fehler=fehler))
    assert "Anmeldung" in waechter.meldung()


def test_kein_ausschluss_wegen_einer_anmeldewand() -> None:
    """**Der teuerste Fall**, deshalb am Quelltext festgehalten.

    Ausgeschlossen wird seit dem 24.09.2026 nur nach einer vollen Suche ohne
    kommentierbaren Beitrag (``automatik.nichts_zu_machen``) - nie wegen
    einer Anmeldewand, auch nicht nach 15 Runden. Was bleibt, ist das
    Anhalten: Der Sitzungsfehler beendet den Lauf, oertlich wie fern.
    """
    quelltext = Path("src/fbgroups/marketing/automatik.py").read_text(encoding="utf-8")
    wand = automatik.Schrittergebnis(
        erfolg=False, fehler=f"{actions.NICHT_ANGEMELDET}: {_wand(ARABISCHE_WAND)}"
    )
    volle_suche = {"runden": 15, "runden_max": 15, "gesehen": 12, "geeignet": False}

    assert automatik.nichts_zu_machen(volle_suche, [{"text": "x"}], wand) == ""
    assert "if ist_sitzungsfehler(ergebnis.fehler):" in quelltext


# --- 3. Der Lauf faengt ohne Anmeldung nicht an --------------------------

class _Kontext:
    """Ein Browser, der genau eine Seite hergibt - die Startseite."""

    def __init__(self, text: str, *, wirft: bool = False) -> None:
        self._text = text
        self._wirft = wirft
        self.geoeffnet: list[str] = []
        self.geschlossen = False

    def new_page(self):  # noqa: ANN201 - ein Stub
        kontext = self

        class _P:
            def goto(self, url: str, **_kwargs) -> None:
                if kontext._wirft:
                    raise RuntimeError("net::ERR_NAME_NOT_RESOLVED")
                kontext.geoeffnet.append(url)

            def wait_for_timeout(self, _ms: int) -> None:
                return None

            def inner_text(self, _was: str) -> str:
                return kontext._text

            def close(self) -> None:
                kontext.geschlossen = True

        return _P()


def test_ist_angemeldet_fragt_die_startseite() -> None:
    """Eine Gruppenseite kann aus vielen Gruenden nicht laden, die Startseite nur aus einem."""
    kontext = _Kontext("Startseite\nWas machst du gerade?")

    angemeldet, hinweis = actions.ist_angemeldet(kontext)

    assert angemeldet is True
    assert hinweis == ""
    assert kontext.geoeffnet == ["https://www.facebook.com/"]
    assert kontext.geschlossen, "die Seite wird wieder zugemacht"


def test_ist_angemeldet_erkennt_die_wand() -> None:
    kontext = _Kontext(ARABISCHE_WAND)

    angemeldet, hinweis = actions.ist_angemeldet(kontext)

    assert angemeldet is False
    assert hinweis.startswith(actions.NICHT_ANGEMELDET)


def test_ein_netzfehler_ist_kein_beleg_fuer_eine_abgelaufene_sitzung() -> None:
    """Dieselbe Zurueckhaltung wie bei ``merke_regeln``.

    Aus einer Seite, die nicht geladen hat, wird kein Urteil - sonst
    verweigerte ein Aussetzer der Leitung den Start, und im Text staende, man
    solle sich anmelden.
    """
    kontext = _Kontext("", wirft=True)

    angemeldet, hinweis = actions.ist_angemeldet(kontext)

    assert angemeldet is True, "im Zweifel wird es versucht"
    assert hinweis, "aber gesagt wird es"


def test_beide_laeufe_pruefen_die_anmeldung_vor_dem_ersten_schritt() -> None:
    """Oertlich wie fern, und **vor** dem ersten Schritt.

    Danach waere es zu spaet: Der erste Fehlschlag traegt die Gruppe schon
    aus der Kampagne.
    """
    cli = Path("src/fbgroups/marketing/cli.py").read_text(encoding="utf-8")

    # Einmal die Definition, zweimal der Aufruf - oertlich und fern.
    assert cli.count("_sitzung_pruefen(context)") == 3, "oertlich und fern"
    fern = cli.split("Fernbetrieb: Stand und Buchung", 1)[1]
    assert fern.index("_sitzung_pruefen(context)") < fern.index("fuehre_lauf_fern_aus")
