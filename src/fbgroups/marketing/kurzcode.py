"""Der oeffentliche Kurzcode zu einem Tracking-Code.

**Warum es ihn gibt.** Der Tracking-Code sagt einem Menschen, der ihn liest,
mehr als er soll: ``FB-SYR-DUE-004-B`` nennt Kanal, Zielgruppe, Stadt und eine
laufende Nummer. In einem Beitrag steht damit die Buchhaltung der Kampagne,
und die Adresse liest sich wie ein Aktenzeichen und nicht wie eine App.

**Was er nicht ist.** Kein zweites Tracking-System. Der Kurzcode ist ein
*Deckname* fuer denselben Code - die Weiterleitung loest ihn auf, zaehlt unter
dem **inneren** Code und schreibt ihn auch so in die Ereignistabelle. In jeder
Auswertung steht weiterhin ``FB-SYR-DUE-004``; was sich aendert, ist allein,
was im Beitrag steht.

**Warum abgeleitet und nicht gewuerfelt.** Ein gewuerfelter Code existiert nur
in der Spalte, in der er steht - geht sie verloren, zeigen alle
veroeffentlichten Beitraege ins Leere. Ein abgeleiteter Code laesst sich aus
dem Tracking-Code und dem Geheimnis jederzeit wieder herstellen. Er wird
trotzdem gespeichert: Die Weiterleitung braucht ihn rueckwaerts, und 300 Codes
bei jedem Klick durchzurechnen waere die teuerste Zeile des Dienstes.

Das Geheimnis (``kurzcode:salt`` in ``marketing_meta``) ist kein Passwort,
sondern verhindert das Gegenteil dieses Moduls: Ohne es koennte jeder, der
einen Beitrag sieht, die Kurzcodes der Nachbargruppen ausrechnen und damit
die Kampagnenstruktur zurueckgewinnen.
"""

from __future__ import annotations

import hashlib
import hmac

#: Das Alphabet des Kurzcodes. Bewusst ohne ``0/o``, ``1/l/i`` und ``u/v``:
#: Ein Mensch, der die Adresse aus einem Beitrag abtippt oder am Telefon
#: weitergibt, verwechselt genau diese - und eine verwechselte Stelle ist
#: kein Tippfehler mit Fehlermeldung, sondern ein Klick, der einer *anderen*
#: Gruppe gutgeschrieben wuerde oder mit 404 endet.
ALPHABET = "23456789abcdefghjkmnpqrstwxyz"

#: Sieben Stellen aus 29 Zeichen sind rund 17 Milliarden Moeglichkeiten. Bei
#: den heute 314 Gruppen ist ein Zusammenstoss damit nicht zu erwarten -
#: behandelt wird er trotzdem (``runde``), denn "unwahrscheinlich" ist keine
#: Zusicherung, und ein zweiter Code auf derselben Adresse zaehlte Klicks der
#: falschen Gruppe zu.
LAENGE = 7


def kurzcode(tracking_code: str, salt: str, *, runde: int = 0, laenge: int = LAENGE) -> str:
    """Der oeffentliche Kurzcode zu einem Tracking-Code.

    Gleiche Eingabe, gleiches Ergebnis - auf jedem Rechner und nach jedem
    Neustart. Derselbe Gedanke wie bei der Vorlagenwahl, die ``blake2b`` und
    nicht das eingebaute ``hash`` benutzt: Was sich zwischen zwei Laeufen
    aendert, taugt nicht als Kennung fuer etwas, das veroeffentlicht wird.

    ``runde`` ist der Ausweg aus einem Zusammenstoss: Sie geht in die
    Ableitung ein, also ergibt Runde 1 einen anderen Code - und zwar wieder
    einen berechenbaren. Der Aufrufer zaehlt hoch, bis die Adresse frei ist.
    """
    roh = hmac.new(
        salt.encode("utf-8"),
        f"{tracking_code}#{runde}".encode(),
        hashlib.sha256,
    ).digest()

    zahl = int.from_bytes(roh, "big")
    zeichen = []
    for _ in range(laenge):
        zahl, rest = divmod(zahl, len(ALPHABET))
        zeichen.append(ALPHABET[rest])
    return "".join(zeichen)


def ist_kurzcode(code: str) -> bool:
    """Ob eine Zeichenfolge nach einem Kurzcode aussieht.

    Nur zur Unterscheidung in Ausgaben gedacht, nie als Ersatz fuer das
    Nachschlagen: Ob ein Code existiert, weiss allein der Bestand. Ein
    Tracking-Code ist grossgeschrieben und traegt Bindestriche, ein Kurzcode
    weder das eine noch das andere - die beiden sind nicht zu verwechseln.
    """
    return (
        len(code) == LAENGE
        and code.islower()
        and all(zeichen in ALPHABET for zeichen in code)
    )


__all__ = ["ALPHABET", "LAENGE", "ist_kurzcode", "kurzcode"]
