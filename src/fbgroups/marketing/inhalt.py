"""Worum geht es in diesem Beitrag? - die Frage vor der Antwort.

## Was dieses Modul ist

Der fehlende Schritt zwischen "hier steht ein Beitrag" und "hier passt eine
Antwort". Es liest einen Text und sagt, **worum** es geht (``Thema``), **was**
die Person will (``Absicht``) und ob es einen echten Bezug zu unserem Angebot
gibt (``bezug``). Es entscheidet **nicht**, ob geantwortet wird - das tut
``entscheidung.py``.

Rein wie ``qualifikation.py``, ``kaltmodus.py`` und ``beitritt.py``: kein
Netz, keine Datenbank, kein Playwright. Der Aufrufer reicht den Text herein
und bekommt einen Befund zurueck. Damit ist jede Regel dieses Moduls ohne
Browser pruefbar.

## Die Grenze des Projekts gilt hier besonders

``CLAUDE.md`` verbietet, **Beitragsinhalte zu lesen oder zu speichern**. Der
zweite Teil bleibt unangetastet, und der erste wird hier so eng wie moeglich
ausgelegt:

* Der Text wird **durchgereicht, nicht gespeichert**. Dieses Modul hat keine
  Datenbank, und was der Aufrufer speichert, ist der **Befund** - ein
  Schlagwort wie ``wohnung`` und ein Urteil wie ``keine``. Nie der Satz, nie
  der Mensch, der ihn geschrieben hat.
* Es gibt dafuer einen Vorlaeufer im Haus: ``actions._artikel_auswerten``
  liest seit jeher ``article.inner_text()``, um Reaktionen und
  Kommentarzahlen zu zaehlen - und behaelt davon nichts. Genau diese
  Aufteilung wird hier fortgesetzt.

Der Grund fuer den Schritt steht in der Anforderung vom 12.09.2026: Ein
Kommentar, der unter jeden Beitrag dasselbe schreibt, ist Spam - und zwar
unabhaengig davon, wie gut der Satz formuliert ist. Wer nicht weiss, worum es
geht, kann nicht entscheiden, ob eine Antwort etwas beitraegt.

## Warum Schlagwoerter und kein Sprachmodell

Dieselbe Ueberlegung wie bei der Textherstellung (siehe "Keine KI (entfernt)"
in ``CLAUDE.md``): Ein kleines Modell, das aus dem Nichts urteilt, erfindet
Zusammenhaenge - und ein erfundener Zusammenhang wird hier zu einem Kommentar
unter einem fremden Beitrag. Schlagwoerter irren sich ebenfalls, aber
nachvollziehbar: Jeder Treffer steht im Befund, und wer ihn liest, sieht,
warum das Urteil so ausfiel.

## Zwei Vergleichsstrategien, wie ueberall im Projekt

Lateinische Begriffe mit Wortgrenze, arabische als Teilstring - im Arabischen
haengen Artikel und Praepositionen am Wort (``textnorm.contains_term``). Wer
das vereinheitlicht, zerstoert die arabische Erkennung.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from fbgroups.textnorm import contains_term, normalize


class Thema(StrEnum):
    """Worum es in einem Beitrag geht.

    Bewusst breit: Die Anforderung nennt ausdruecklich, dass die Analyse
    **nicht** auf "Versand nach Syrien" beschraenkt sein darf. Eine Gruppe
    fuer arabische Gemeinschaft in Bremen redet ueber Wohnungen, Jobs,
    Behoerden und Autos - und ein Runner, der davon nur eines erkennt, haelt
    alles Uebrige faelschlich fuer seine Gelegenheit.

    ``UNLESBAR`` ist kein Thema, sondern das Eingestaendnis, dass nichts
    dastand: ein leerer Text, ein Bild ohne Worte. Es fuehrt zu ``NO_REPLY``
    und nicht zu einem geratenen Urteil.
    """

    VERSAND = "versand"
    REISE = "reise"
    WOHNUNG = "wohnung"
    JOB = "job"
    KAUF_VERKAUF = "kauf_verkauf"
    DIENSTLEISTUNG = "dienstleistung"
    BEHOERDE = "behoerde"
    EMPFEHLUNG = "empfehlung"
    HILFE = "hilfe"
    SONSTIGES = "sonstiges"
    UNLESBAR = "unlesbar"


class Absicht(StrEnum):
    """Was die Person will - und das ist eine andere Frage als das Thema.

    "Ich **suche** eine Wohnung" und "Ich **biete** eine Wohnung" haben
    dasselbe Thema und verlangen das Gegenteil voneinander. Ohne diese
    Unterscheidung antwortete der Runner einem Anbieter mit einem Angebot.
    """

    SUCHT = "sucht"
    BIETET = "bietet"
    FRAGT = "fragt"
    UNBEKANNT = "unbekannt"


class Anlass(StrEnum):
    """Der konkrete Fall, auf den eine Vorlage antwortet.

    **Die Bruecke zwischen Beitrag und Text** (13.09.2026). Thema und Absicht
    sagen, worum es geht und was jemand will; der Anlass sagt, *welche der
    vorbereiteten Antworten* darauf passt. Ohne ihn stand unter jedem
    ausgewaehlten Beitrag derselbe Text aus dem Kampagnenvorrat - der war
    ordentlich formuliert, aber er antwortete auf nichts.

    Er ist damit auch die Schranke, die die Anforderung verlangt: *"Ich
    fliege naechste Woche nach Syrien"* ergibt **keinen** Anlass und damit
    keinen Kommentar. *"... und habe noch Platz im Koffer"* ergibt
    ``PLATZ_IM_KOFFER``. Derselbe Satz, ein Halbsatz mehr, und erst der macht
    aus einer Reiseankuendigung eine Gelegenheit.

    ``KEINER`` ist deshalb ein Ergebnis wie ``NO_REPLY``: Es gibt hier nichts
    zu sagen, was jemandem naeher braechte, was er sucht.
    """

    KEINER = "keiner"
    SUCHT_REISENDEN = "sucht_reisenden"
    GESCHENK = "geschenk"
    GEGENSTAND = "gegenstand"
    VERSANDWEG = "versandweg"
    PLATZ_IM_KOFFER = "platz_im_koffer"
    BIETET_MITNAHME = "bietet_mitnahme"
    MEDIKAMENTE = "medikamente"


class Relevanz(StrEnum):
    """Wie gut der Beitrag zu **unserem** Angebot passt.

    ``HOCH`` heisst: Was die Person sucht, ist genau das, was die App
    vermittelt. ``MITTEL``: Es gibt eine Beruehrung, aber keinen Beleg.
    ``KEINE``: Es gibt keinen Zusammenhang, und dann ist jede Antwort Werbung
    an der falschen Stelle.
    """

    HOCH = "hoch"
    MITTEL = "mittel"
    KEINE = "keine"


# --- Was in den Beitraegen steht -------------------------------------------
#
# Deutsch, Arabisch und etwas Englisch nebeneinander, weil die Gruppen des
# Bestands so schreiben. Arabisch ohne Wortgrenze (``contains_term`` waehlt
# die Strategie selbst); die Formen sind absichtlich kurz gehalten, damit
# Praefixe wie ``بال`` oder ``لل`` sie nicht verstecken.

#: Der Kern unseres Angebots: etwas von Deutschland nach Hause mitgeben.
_VERSAND = (
    "paket", "pakete", "versand", "verschicken", "schicken", "senden",
    "mitnehmen", "mitgeben", "kurier", "sendung",
    "parcel", "shipping", "send to syria",
    "طرد", "طرود", "شحن", "ابعت", "بعت", "يبعت", "ارسال", "إرسال",
    "توصيل", "امانة", "أمانة", "امانات", "أمانات", "غرض صغير", "اغراض",
    "أغراض", "مشوار",
    # 13.09.2026: Die Verbformen fehlten. "ارسال" ist das Substantiv und
    # steckt NICHT in "ارسل" - der arabische Teilstringabgleich laeuft nur in
    # eine Richtung. "بدي ارسل وثائق من المانيا ع سوريا" war damit thematisch
    # unerkannt, also ohne Bezug und ohne Anlass: genau der Beitrag, fuer den
    # es die App gibt.
    "ارسل", "ترسل", "يرسل", "نرسل", "رسلي", "ابعتلك", "بعتلي",
)
#: Die Bewegungswoerter der syrischen Umgangssprache - **eine Haelfte** der
#: Kombination, die eine Reiseankuendigung ausmacht. Allein sagen sie nichts:
#: "رايح ع الشغل" ist kein Reiseanlass, und genau deshalb steht die andere
#: Haelfte (``_ZIELE``) daneben.
_BEWEGUNG = (
    "نازل", "نازلة", "طالع", "طالعة", "رايح", "رايحة", "راجع", "راجعة",
    "جاي", "جاية", "واصل", "واصلة", "مسافر", "مسافرة", "بسافر", "مسافرين",
    "بنزل", "برجع", "طاير",
    # 21.09.2026: **Die Rueckreise fehlte.** In "مين نازل على الشام" stand
    # "عوده من حلب والشام الى المانيا بعد 3 ايام" - eine Fahrt mit Ziel und
    # Datum, und trotzdem "sonstiges": Kein Wort davon war eine Bewegung.
    # Dabei ist der Rueckweg dieselbe Gelegenheit wie der Hinweg - wer aus
    # Syrien kommt, kann von dort etwas mitbringen.
    "عوده", "عودة", "رجعه", "رجعة", "رجوع", "عائد", "عايد", "راجعين",
)

#: Zeitangaben, wie sie in diesen Beitraegen neben der Reise stehen.
#:
#: **Sie begruenden allein keinen Anlass** und stehen deshalb in keiner
#: Themenliste: "بكرا" heisst morgen, und die meisten Beitraege einer Gruppe
#: handeln von morgen. Sie sind der Beleg dafuer, dass eine Reise
#: tatsaechlich bevorsteht - gefragt werden sie nur **zusammen** mit einem
#: Bewegungswort (``erkenne_anlass``).
_ZEITNAH = (
    "بكرا", "بعد بكرا", "هالجمعة", "هالاسبوع", "هالأسبوع",
    "الاسبوع الجاي", "الأسبوع الجاي", "يوم الجمعة", "يوم السبت",
    "يوم الاحد", "يوم الأحد", "اليوم", "هالشهر", "نهاية الاسبوع",
    "نهاية الأسبوع",
)


_REISE = (
    "reise", "reisen", "flug", "fluege", "koffer", "gepaeck", "ticket",
    "travel", "flight", "luggage", "baggage",
    # "flieg" statt "fliege": Der lateinische Abgleich erlaubt die
    # Wortfortsetzung, also trifft es fliege/fliegen/fliegt/Flieger auf
    # einmal. "flug" tut das NICHT fuer "fluege" - daher stehen beide da.
    "flieg", "abflug", "heimreise", "urlaub",
    "سفر", "مسافر", "مسافرين", "رحلة", "طيارة", "طيران", "شنطة", "حقيبة",
    "عفش", "تذكرة",
    # 21.09.2026: Wie die Reisenden selbst schreiben. "مرحبا نازلة من
    # ألمانيا عالشام ب 27/9 متوفر وزن خفيف" trug kein einziges Wort von
    # oben - der Beitrag galt als "sonstiges", also ohne Bezug, und der Lauf
    # ging an ihm vorbei. Dabei ist er der Beitrag, fuer den es die App gibt:
    # ein Reisender mit freiem Gepaeck.
    #
    # Es ist syrische Umgangssprache und kein Hocharabisch: "نازل/نازلة"
    # (hinunter nach Syrien), "طالع" (hinauf nach Europa), "رايح/جاي/راجع",
    # dazu "وزن" (Gepaeckgewicht), "مطار" und "حجز".
    #
    # **Thema ist nicht Anlass.** Diese Woerter sagen nur, wovon ein Beitrag
    # handelt. Ob daraus eine Gelegenheit wird, entscheidet
    # ``erkenne_anlass`` - dort braucht es ein **Ziel** dazu oder einen
    # Halbsatz ueber freies Gepaeck. "وزن" allein bleibt damit folgenlos:
    # Reise ohne Ziel ist ``Relevanz.KEINE``.
    *_BEWEGUNG,
    "وزن", "مطار", "بالمطار", "عالطيارة", "بالطائرة", "حجز", "ترانزيت",
    "رحلتي", "وصلت",
)
_WOHNUNG = (
    "wohnung", "wohnungen", "zimmer", "wg", "miete", "mieten", "apartment",
    "unterkunft", "kaution", "wohnungssuche", "nachmieter",
    "flat", "room",
    "شقة", "شقق", "غرفة", "سكن", "بيت", "ايجار", "إيجار", "كراء",
)
_JOB = (
    "job", "jobs", "arbeit", "stelle", "stellen", "minijob", "ausbildung",
    "bewerbung", "lebenslauf", "gehalt", "arbeitsvertrag", "praktikum",
    "work", "vacancy", "hiring",
    "شغل", "عمل", "وظيفة", "وظائف", "دوام", "تدريب", "راتب", "سيرة ذاتية",
)
_KAUF_VERKAUF = (
    "verkaufe", "verkauf", "kaufen", "gebraucht", "abzugeben", "preis",
    "auto", "moebel", "sofa", "handy", "laptop", "fahrrad",
    "for sale", "selling", "buy",
    "للبيع", "بيع", "شراء", "مستعمل", "سعر", "سيارة", "اثاث", "أثاث",
    "موبايل", "جوال", "لابتوب", "بسكليت",
)
_DIENSTLEISTUNG = (
    "uebersetzung", "uebersetzer", "anwalt", "friseur", "umzug", "reparatur",
    "handwerker", "versicherung", "fahrschule", "nachhilfe",
    "service", "translation", "lawyer",
    "ترجمة", "مترجم", "محامي", "حلاق", "نقل عفش", "تصليح", "تامين", "تأمين",
    "مدرسة سواقة", "دروس",
)
_BEHOERDE = (
    "jobcenter", "auslaenderbehoerde", "amt", "antrag", "termin",
    "aufenthalt", "visum", "botschaft", "konsulat", "einbuergerung",
    "anmeldung", "krankenkasse", "steuer",
    "appointment", "embassy", "residence permit",
    "جوب سنتر", "دائرة", "اقامة", "إقامة", "فيزا", "سفارة", "قنصلية",
    "معاملة", "موعد", "جنسية", "تامين صحي", "ضريبة",
)
_EMPFEHLUNG = (
    "empfehlung", "empfehlt", "empfehlen", "erfahrung", "erfahrungen",
    "welcher", "welche ist besser", "tipp", "tipps",
    "recommend", "experience", "best",
    "ينصح", "نصيحة", "تجربة", "تجارب", "احسن", "أحسن", "افضل", "أفضل",
    "شو رايكم", "شو رأيكم",
)
_HILFE = (
    "hilfe", "helfen", "bitte um hilfe", "dringend", "notfall",
    "help", "urgent",
    "مساعدة", "ساعدوني", "بليز", "ضروري", "عاجل", "محتاج مساعدة",
)

#: Die Reihenfolge entscheidet bei mehreren Treffern, und sie ist begruendet:
#: Versand und Reise stehen vorn, weil sie das Angebot der App betreffen -
#: ein Beitrag, der beides nennt ("Ich reise nach Damaskus, kann was
#: mitnehmen"), ist fuer uns ein Versandbeitrag. Danach die konkreten
#: Lebenslagen, zuletzt die allgemeinen.
#:
#: **Behoerde steht vor Job**, und das ist kein Geschmack: Der lateinische
#: Abgleich erlaubt die Wortfortsetzung ("arab" trifft "araber"), also trifft
#: ``job`` auch ``jobcenter`` - und "Wer kennt einen Termin beim Jobcenter?"
#: stuende als Stellenangebot im Protokoll. Dieselbe Art Kollision wie
#: zwischen der Stadt "Essen" und dem Kategoriebegriff fuer Speisen; ein
#: Abgleich aller Listen gegeneinander fand genau diese eine.
_THEMEN: tuple[tuple[Thema, tuple[str, ...]], ...] = (
    (Thema.VERSAND, _VERSAND),
    (Thema.REISE, _REISE),
    (Thema.WOHNUNG, _WOHNUNG),
    (Thema.BEHOERDE, _BEHOERDE),
    (Thema.JOB, _JOB),
    (Thema.KAUF_VERKAUF, _KAUF_VERKAUF),
    (Thema.DIENSTLEISTUNG, _DIENSTLEISTUNG),
    (Thema.HILFE, _HILFE),
    (Thema.EMPFEHLUNG, _EMPFEHLUNG),
)

_SUCHT = (
    "suche", "gesucht", "brauche", "benoetige", "wer hat", "wer kann",
    "kennt jemand", "gibt es jemand", "hilfe gesucht",
    "looking for", "need", "anyone",
    "بدي", "ابحث", "أبحث", "محتاج", "مين عنده", "في حدا", "فيه حدا",
    "حدا عنده", "لو سمحتو", "مطلوب",
)
_BIETET = (
    "biete", "anzubieten", "verkaufe", "vermiete", "abzugeben", "frei ab",
    "habe noch", "offering", "available",
    "عندي", "متوفر", "متاح", "للبيع", "للايجار", "للإيجار", "بقدم",
    "بعرض", "فاضي عندي",
)
_FRAGT = (
    "?", "frage", "wie kann", "wie geht", "was kostet", "weiss jemand",
    "how can", "how much", "does anyone know",
    "؟", "كيف", "شو", "وين", "قديش", "كم سعر", "ليش", "هل",
)

#: Wo der Weg hingeht, wenn das Thema Versand oder Reise ist. Nur wenn
#: **beides** zusammenkommt - ein Ziel und ein Versand-/Reisethema -, gibt es
#: einen echten Bezug zur App; sonst ist "ich reise nach Berlin" kein Anlass.
_ZIELE = (
    "syrien", "syria", "damaskus", "aleppo", "homs", "latakia",
    "irak", "iraq", "bagdad", "libanon", "beirut", "jordanien", "amman",
    "aegypten", "kairo", "marokko", "tunesien", "algerien",
    "سوريا", "سورية", "الشام", "دمشق", "حلب", "حمص", "اللاذقية", "طرطوس",
    "العراق", "بغداد", "لبنان", "بيروت", "الاردن", "الأردن", "عمان",
    "مصر", "القاهرة", "المغرب", "تونس", "الجزائر", "الوطن", "البلد",
)

#: Woher - die andere Haelfte der Strecke. Ein Beitrag, der Deutschland und
#: ein Ziel nennt, beschreibt genau den Weg, den die App vermittelt.
_HERKUNFT = (
    "deutschland", "germany", "berlin", "hamburg", "muenchen", "koeln",
    "frankfurt", "stuttgart", "bremen", "hannover", "essen", "dortmund",
    "almanya", "المانيا", "ألمانيا", "برلين", "هامبورغ", "ميونخ", "كولن",
    "فرانكفورت", "شتوتغارت", "بريمن", "هانوفر",
)


# --- Woran der Anlass erkannt wird -----------------------------------------
#
# Kurz gehalten und eng gefasst, denn hier entsteht der Unterschied zwischen
# einer Antwort und keiner. Ein zu weiter Begriff macht aus jedem Beitrag eine
# Gelegenheit - und das ist genau der Automat, den die Anforderung nicht will.

#: Medikamente. Eigener Anlass, weil die Antwort dazu einen Satz mehr braucht:
#: Was ueber die Grenze darf, entscheidet nicht die App.
_MEDIKAMENTE = (
    "medikament", "medikamente", "medizin", "arznei", "tabletten",
    "medicine", "medication", "drugs",
    "دواء", "ادوية", "دوا", "علاج", "وصفة",
)

#: Papiere - gehoeren zum kleinen Gegenstand, stehen aber getrennt, weil sie
#: in den Beitraegen des Bestands haeufig und ausdruecklich genannt werden.
_DOKUMENTE = (
    "dokument", "dokumente", "unterlagen", "papiere", "urkunde", "vollmacht",
    "document", "documents", "papers",
    "وثيقة", "وثائق", "اوراق", "ورقة", "شهادة", "وكالة", "معاملة ورق",
)

#: Freier Platz im Gepaeck - der Halbsatz, der aus einer Reiseankuendigung
#: eine Gelegenheit macht. Ohne ihn ist "ich fliege nach Damaskus" nur eine
#: Mitteilung, und darauf zu antworten waere eingeworfene Werbung.
_PLATZ = (
    "platz im koffer", "platz in der tasche", "freier platz", "platz frei",
    "noch platz", "habe platz", "hab platz", "platz uebrig", "platz fuer",
    "uebergepaeck", "gepaeck frei", "kilo frei",
    "free space", "extra baggage", "spare space", "extra kilos",
    "مساحة بالشنطة", "مكان بالشنطة", "مجال بالشنطة", "مساحة بالحقيبة",
    "مكان بالحقيبة", "مجال بالحقيبة", "وزن زيادة", "كيلو زيادة",
    "وزن فاضي", "شنطة فاضية", "عندي مساحة", "عندي مجال",
    # Aus den Beitraegen des Bestands (21.09.2026): In den Reisegruppen
    # heisst freies Gepaeck fast immer "وزن" - "متوفر وزن خفيف", "معي وزن",
    # "في وزن". Ohne diese Zeilen trug der haeufigste Beitrag dieser Gruppen
    # keinen Anlass, und der Lauf ging an ihm vorbei.
    #
    # **Jede Zeile traegt ihren Zusammenhang mit sich.** "وزن" allein steht
    # hier nicht, "مجال" und "مساحة" auch nicht: "في مجال العمل" ist ein
    # Stellenangebot, und ein Kommentar ueber Koffer waere dort eingeworfene
    # Werbung. Was hier steht, ist immer Besitz ("معي", "عندي", "ضايل") oder
    # der Ort ("بالشنطة", "بالحقيبة").
    "متوفر وزن", "وزن متوفر", "عندي وزن", "معي وزن", "في وزن", "بقي وزن",
    "وزن خفيف", "وزن متاح", "باقي وزن", "ضايل وزن", "ضلّ وزن", "ضل وزن",
    "معي مجال", "معي مساحة", "معي مكان", "معي كيلو", "معي كم كيلو",
    "عندي كيلو", "عندي كيلوات", "عندي كم كيلو", "ضايل كم كيلو",
    "مجال بالشنطة", "مجال بالحقيبة", "مساحة بالشنطة", "مساحة بالحقيبة",
    "مكان بالشنطة", "مكان بالحقيبة", "مجال بالجنطة", "مساحة بالجنطة",
    "شنطة فاضي", "جنطة فاضية", "كيلو فاضي", "كيلوات فاضية",
    # "Ich kann etwas mitnehmen" - dieselbe Gelegenheit, aus Sicht des
    # Reisenden formuliert.
    "فيني اخد غرض", "فيني آخد غرض", "بقدر اخد غرض", "بقدر آخد غرض",
    "فيني اخد", "فيني آخد", "بقدر اخد امانة", "بقدر آخد امانة",
    "في مجال اخد", "في مجال آخد",
)

#: Jemand sucht einen Reisenden - die haeufigste Gelegenheit ueberhaupt und
#: genau die Frage, die die App beantwortet.
_SUCHT_REISENDEN = (
    "wer fliegt", "wer reist", "wer faehrt nach", "jemand der fliegt",
    "reisenden gesucht", "suche jemanden der", "wer kann mitnehmen",
    "wer nimmt mit", "wer geht nach",
    "anyone traveling", "anyone flying", "looking for a traveler",
    "مين مسافر", "حدا مسافر", "في حدا رايح", "فيه حدا رايح", "حدا نازل",
    "مين نازل", "مين رايح", "حدا رايح", "بدي حدا ياخد", "حدا بياخد",
    "مين بياخد", "مين ممكن ياخد",
)

#: Geschenke - eigener Anlass, weil die Antwort darauf einen anderen Ton hat
#: als die auf ein Paket: Es geht um die Familie, nicht um Logistik.
_GESCHENK = (
    "geschenk", "geschenke", "praesent", "gift", "gifts", "present",
    "هدية", "هدايا", "هديه",
)

#: Die Frage nach dem Weg selbst: "wie schicke ich das ueberhaupt?"
_VERSANDWEG = (
    "wie kann ich schicken", "wie verschicke", "wie schicke ich",
    "versandmoeglichkeit", "beste moeglichkeit", "welche firma",
    "was kostet der versand", "wie teuer ist",
    "how can i send", "best way to send", "shipping option",
    "كيف ابعت", "كيف ارسل", "شو افضل طريقة", "افضل طريقة لارسال",
    "طريقة شحن", "شركة شحن", "قديش الشحن", "كم سعر الشحن", "شو الحل لارسال",
)


@dataclass(frozen=True)
class Inhaltsbefund:
    """Was in einem Beitrag steht - als Urteil, nicht als Abschrift.

    **Dies ist das Einzige, was gespeichert werden darf.** Der Text selbst
    bleibt im Browser: Ein Schlagwort und ein Urteil sagen alles, was fuer die
    Entscheidung gebraucht wird, und nichts ueber den Menschen, der geschrieben
    hat.

    ``treffer`` traegt die Begriffe, die zum Urteil gefuehrt haben - dieselbe
    Regel wie bei ``Group.score_reason`` und ``qualifikation.Befund.grund``:
    Eine Einstufung, deren Begruendung man nicht nachlesen kann, wird nicht
    nachgeschlagen, sondern geglaubt.
    """

    thema: Thema = Thema.UNLESBAR
    absicht: Absicht = Absicht.UNBEKANNT
    relevanz: Relevanz = Relevanz.KEINE
    treffer: tuple[str, ...] = field(default_factory=tuple)
    #: Nennt der Beitrag ein Ziel **und** eine deutsche Herkunft? Dann
    #: beschreibt er die Strecke, die die App vermittelt.
    strecke: bool = False

    anlass: Anlass = Anlass.KEINER
    """Welche der vorbereiteten Antworten hier passt - oder keine.

    Steht neben ``relevanz`` und nicht darin: Jenes sagt, **ob** der Beitrag
    etwas mit uns zu tun hat, dieses, **was** man dazu sagen koennte. Ein
    Beitrag kann hohe Relevanz haben und trotzdem keinen Anlass tragen
    ("ich fliege naechste Woche nach Damaskus") - dann gibt es nichts zu
    sagen, was jemandem naeher braechte, was er sucht.
    """

    @property
    def lesbar(self) -> bool:
        return self.thema is not Thema.UNLESBAR

    @property
    def grund(self) -> str:
        """Eine Zeile fuer das Protokoll - Thema, Absicht und die Treffer."""
        if not self.lesbar:
            return "kein lesbarer Text"
        kern = f"{self.thema.value}/{self.absicht.value}"
        if self.anlass is not Anlass.KEINER:
            kern += f"/{self.anlass.value}"
        treffer = ", ".join(self.treffer[:4])
        return f"{kern} ({treffer})" if treffer else kern


def _treffer(text: str, begriffe: tuple[str, ...]) -> list[str]:
    """Welche der Begriffe vorkommen - mit der Strategie ihrer Schrift."""
    return [begriff for begriff in begriffe if contains_term(text, begriff)]


def lies(text: str) -> Inhaltsbefund:
    """Den Befund zu **einem** Beitrag oder Kommentar. Rein, ohne Speicher.

    Der Text geht hier hinein und nirgendwo sonst hin: Was zurueckkommt, ist
    ein Urteil aus Schlagwoertern. Ein leerer oder zu kurzer Text ergibt
    ``UNLESBAR`` - und das ist eine Aussage ueber **unsere** Lesbarkeit, nicht
    ueber den Beitrag. Sie fuehrt zu ``NO_REPLY``, nicht zu einem geratenen
    Thema; dieselbe Regel wie bei ``Regelbefund.gelesen``.
    """
    roh = (text or "").strip()
    # Drei Zeichen sind kein Beitrag, sondern ein Bild mit einem Smiley
    # daneben. Daraus ein Thema zu raten hiesse, den Zufall zu befragen.
    if len(roh) < 8:
        return Inhaltsbefund()

    normal = normalize(roh)
    gefunden: list[str] = []
    thema = Thema.SONSTIGES
    for kandidat, begriffe in _THEMEN:
        if treffer := _treffer(normal, begriffe):
            thema = kandidat
            gefunden = treffer
            break

    # **Ein Gepaeckhalbsatz bringt sein Thema mit** (21.09.2026). "بقي معي
    # كم كيلو" und "في مجال آخد غرض" nennen weder Reise noch Versand, und
    # die Themenerkennung liess sie deshalb als "sonstiges" liegen - obwohl
    # sie die deutlichste Gelegenheit ueberhaupt sind: freier Platz im
    # Koffer. Dieselbe Ueberlegung wie bei ``_SUCHT_REISENDEN`` in
    # ``erkenne_anlass``: Wer den Satz schreibt, hat sein Thema geliefert,
    # auch wenn keine Wortliste es aufgefangen hat.
    #
    # Nur aus ``SONSTIGES`` heraus: Ein Beitrag ueber eine Wohnung bleibt
    # eine Wohnung, auch wenn das Wort "مساحة" darin vorkommt.
    if thema is Thema.SONSTIGES and (platz := _treffer(normal, _PLATZ)):
        thema = Thema.REISE
        gefunden = platz

    absicht = Absicht.UNBEKANNT
    if _treffer(normal, _SUCHT):
        absicht = Absicht.SUCHT
    elif _treffer(normal, _BIETET):
        absicht = Absicht.BIETET
    elif _treffer(normal, _FRAGT) or "?" in roh or "؟" in roh:
        absicht = Absicht.FRAGT

    ziel = bool(_treffer(normal, _ZIELE))
    herkunft = bool(_treffer(normal, _HERKUNFT))
    strecke = ziel and herkunft

    anlass = erkenne_anlass(normal, thema, absicht)
    relevanz = _relevanz(thema, absicht, ziel=ziel, strecke=strecke)
    if anlass is not Anlass.KEINER and relevanz is Relevanz.KEINE:
        # **Ein erkannter Anlass ist selbst der Beleg** (21.09.2026).
        # "معي وزن متوفر" nennt kein Ziel - es steht in einer Reisegruppe,
        # dort ist das selbstverstaendlich. Die Relevanzstufe kennt die
        # Gruppe aber nicht, und so fiel die deutlichste Gelegenheit
        # ueberhaupt durch die Schwelle, bevor der Anlass ueberhaupt gefragt
        # wurde. Dieselbe Begruendung steht seit dem 13.09.2026 in
        # ``entscheidung.soll_app_nennen`` ("Belegt heisst HOCH **oder** ein
        # erkannter Anlass") - sie wirkte nur eine Stufe zu spaet.
        #
        # Gehoben wird auf ``MITTEL`` und nicht auf ``HOCH``: In einer
        # Gemeinschaftsgruppe (Schwelle "hoch") bleibt es damit bei nichts,
        # und der Ort entscheidet weiter.
        relevanz = Relevanz.MITTEL

    return Inhaltsbefund(
        thema=thema,
        absicht=absicht,
        relevanz=relevanz,
        treffer=tuple(gefunden[:6]),
        strecke=strecke,
        anlass=anlass,
    )


def _reise_mit_ziel(normal: str) -> bool:
    """Nennt der Beitrag **Bewegung und Ziel**? Dann steht eine Fahrt an.

    Die Antwort auf die Forderung, einzelne Woerter nicht ausreichen zu
    lassen (21.09.2026): "سوريا" allein ist ein Land, "نازل" allein ist ein
    Weg zur Arbeit - erst zusammen sind sie eine Ankuendigung. Gefragt wird
    nach dem Vorkommen im selben Beitrag und nicht nach der Reihenfolge:
    Geschrieben wird "نازلة من ألمانيا عالشام", "رحلتي ع دمشق" und "مسافر
    يوم الجمعة ع حلب", und eine Liste fertiger Wendungen traefe immer nur
    die, an die jemand gedacht hat. Der arabische Abgleich laeuft ohne
    Wortgrenze, deshalb sind "عالشام", "ع الشام" und "للشام" mitgemeint.

    Eine **Zeitangabe** ersetzt das Ziel nicht. Sie steht in ``_ZEITNAH``
    und dient dem Anlasstext, nicht der Schranke: "مسافر بكرا" kann jede
    Fahrt meinen.
    """
    return bool(_treffer(normal, _BEWEGUNG)) and bool(_treffer(normal, _ZIELE))


def erkenne_anlass(normal: str, thema: Thema, absicht: Absicht) -> Anlass:
    """Welcher der vorbereiteten Faelle hier vorliegt - oder keiner.

    ``normal`` ist der bereits normalisierte Text; Thema und Absicht kommen
    aus ``lies``. Eigene Funktion, damit die Zuordnung einzeln pruefbar ist:
    Sie entscheidet, welcher Satz unter einem fremden Beitrag steht.

    **Die Reihenfolge ist die Genauigkeit**, von der engsten Beobachtung zur
    weitesten. Der erste Treffer gewinnt, und das ist Absicht: "Ich reise am
    Dienstag und kann Medikamente mitnehmen" traegt drei Anzeichen, und die
    Antwort darauf soll die zum Medikament sein - sie ist die einzige, die
    etwas sagt, was die anderen nicht auch sagen.

    **Ein Anlass entsteht nur bei Versand- oder Reisethema.** Ohne das ist
    ein Geschenk ein Geburtstag und ein Dokument ein Behoerdengang; die
    Woerter allein belegen nichts. Die einzige Ausnahme ist die ausdrueckliche
    Suche nach einem Reisenden - wer "مين مسافر ع الشام" schreibt, hat sein
    Thema mitgeliefert, auch wenn die Themenerkennung es anders einsortiert.
    """
    sucht_reisenden = bool(_treffer(normal, _SUCHT_REISENDEN))
    if sucht_reisenden and thema not in (Thema.VERSAND, Thema.REISE):
        # Der Satz nennt die Sache beim Namen. Ihn an der Themenerkennung
        # scheitern zu lassen hiesse, die deutlichste Gelegenheit wegen einer
        # Einordnung zu verlieren, die nur die Reihenfolge einer Liste ist.
        return Anlass.SUCHT_REISENDEN

    if thema not in (Thema.VERSAND, Thema.REISE):
        return Anlass.KEINER

    if _treffer(normal, _MEDIKAMENTE):
        return Anlass.MEDIKAMENTE
    if _treffer(normal, _PLATZ):
        # Steht sowohl beim Reisenden ("hab noch Platz") als auch beim
        # Suchenden ("wer hat Platz?"). Beide Male ist es derselbe Anlass:
        # Im Koffer ist Raum, und wir kennen die andere Haelfte.
        return Anlass.PLATZ_IM_KOFFER
    if sucht_reisenden:
        return Anlass.SUCHT_REISENDEN
    if _treffer(normal, _GESCHENK):
        return Anlass.GESCHENK
    if _treffer(normal, _VERSANDWEG):
        return Anlass.VERSANDWEG

    if thema is Thema.REISE:
        # Ein Reisender, der **Bewegung und Ziel** nennt, ist die andere
        # Haelfte des Marktplatzes: "نازل ع الشام يوم الجمعة" sagt, dass
        # jemand fahren wird und wohin - mehr braucht es nicht, um zu
        # wissen, dass er etwas mitnehmen koennte.
        #
        # **Das kehrt die Regel vom 13.09.2026 fuer diesen einen Fall um**
        # (Anweisung des Nutzers, 21.09.2026). Dort galt "ich fliege
        # naechste Woche nach Syrien" ausdruecklich als blosse Mitteilung.
        # Im Betrieb war es der haeufigste Beitrag der Reisegruppen, und die
        # Gruppen stehen genau dafuer: In einer Gruppe namens "مسافر من
        # أوروبا إلى سوريا" ist die Ankuendigung einer Fahrt kein Small Talk.
        # Die Schranke bleibt die **Kombination**: ein Bewegungswort allein
        # ("رايح ع الشغل") und ein Zielwort allein ("سوريا حلوة") ergeben
        # weiterhin nichts.
        if _reise_mit_ziel(normal):
            return Anlass.BIETET_MITNAHME
        if absicht is Absicht.BIETET:
            return Anlass.BIETET_MITNAHME
        return Anlass.KEINER

    # Bleibt das Versandthema: jemand will etwas Kleines auf den Weg bringen.
    if absicht in (Absicht.SUCHT, Absicht.FRAGT):
        return Anlass.GEGENSTAND
    if _treffer(normal, _DOKUMENTE):
        return Anlass.GEGENSTAND
    if absicht is Absicht.BIETET:
        return Anlass.BIETET_MITNAHME
    return Anlass.KEINER


def _relevanz(thema: Thema, absicht: Absicht, *, ziel: bool, strecke: bool) -> Relevanz:
    """Wie gut das zu **unserem** Angebot passt - und sonst gar nichts.

    Die App vermittelt Reisende, die eine Kleinigkeit mitnehmen. Daraus folgt
    die ganze Tabelle:

    * **Hoch** ist nur, wo jemand genau das sucht: ein Weg fuer ein Paket,
      belegt durch die Strecke oder wenigstens durch ein genanntes Ziel.
    * **Mittel** ist ein Reisethema ohne erkennbares Ziel - da *koennte* ein
      Bezug sein, aber behauptet ist er nicht.
    * **Keine** ist alles Uebrige, und das ist der haeufigste Fall: Eine
      Wohnungssuche hat mit uns nichts zu tun, und eine Antwort darauf waere
      Werbung an der falschen Stelle. Genau deshalb gibt es diese Stufe -
      nicht jede Gelegenheit ist eine.
    """
    if thema is Thema.VERSAND:
        if strecke or ziel:
            return Relevanz.HOCH
        return Relevanz.MITTEL
    if thema is Thema.REISE:
        # Ein Reisender mit Ziel ist die andere Haelfte des Marktplatzes: Er
        # kann etwas mitnehmen. Ohne Ziel ist "Reise" ein Urlaubsgespraech.
        if strecke:
            return Relevanz.HOCH
        return Relevanz.MITTEL if ziel else Relevanz.KEINE
    if thema is Thema.EMPFEHLUNG and ziel:
        # "Welche Firma schickt gut nach Syrien?" traegt das Thema
        # Empfehlung, meint aber uns - erkennbar nur am Ziel.
        return Relevanz.MITTEL
    return Relevanz.KEINE


__all__ = [
    "Absicht",
    "Anlass",
    "Inhaltsbefund",
    "Relevanz",
    "Thema",
    "erkenne_anlass",
    "lies",
]
