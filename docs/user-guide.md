# Household Tasks

Die Integration erzeugt native Home-Assistant-To-dos, verschickt
Push-Nachrichten und verarbeitet individuelle Eskalationsregeln. Personen,
Aufgabenvorlagen und Regeln werden ausschließlich über die UI verwaltet und
von Home Assistant intern gespeichert.

## Installation und einmalige Einrichtung

Empfohlen wird die Installation über HACS:

1. In HACS unter **Integrationen** das Repository
   `https://github.com/domschmidt/ha-household-tasks` als benutzerdefiniertes
   Repository vom Typ **Integration** hinzufügen.
2. **Household Tasks** herunterladen und Home Assistant neu starten.
3. **Einstellungen > Geräte & Dienste > Integration hinzufügen** öffnen.
4. Nach **Household Tasks** suchen.
5. Die native To-do-Liste für den Haushalt auswählen.
6. Anschließend erscheint **Aufgaben** in der Seitenleiste.

Bei einer manuellen Installation wird ausschließlich der Ordner
`custom_components/household_tasks` nach
`/config/custom_components/household_tasks` kopiert und Home Assistant neu
gestartet.

Es wird kein Eintrag in `configuration.yaml` benötigt.

## Bedienung

Das Seitenleisten-Panel enthält:

- **Heute**: Familien-Ranking sowie offene, überfällige und kommende Aufgaben
- **Schnellaufgabe**: einmalige Aufgaben für sich selbst oder andere
- **Aufgaben**: wiederverwendbare und geplante Vorlagen
- **Personen**: Push-, Anwesenheits- und Benutzerzuordnungen
- **Verlauf**: erledigte Aufgaben der letzten 90 Tage
- **Einstellungen**: globale Eskalationsregeln, Druckerüberwachung und
  Ausgangswerte

Alle Benutzer mit Steuerberechtigung für die ausgewählte To-do-Entität können
Aufgaben erledigen, Schnellaufgaben hinzufügen und aktive Vorlagen sofort
auslösen. Nur Administratoren dürfen Personen, Vorlagen und Regeln verändern.

## Zeitpläne

Der Aufgabeneditor unterstützt:

- manuell
- wöchentlich mit mehreren Wochentagen
- monatlich
- jährlich
- alle N Monate, zum Beispiel halbjährlich
- Kalenderereignisse mit Suchmuster und zeitlichem Versatz
- einmal täglich nach einem oder mehreren Zustandswechseln
- bei jedem passenden Zustandswechsel mit optionaler Verzögerung und Cooldown
- erneut in einem festen Abstand nach der tatsächlichen letzten Erledigung

### Abhängige Aufgaben

Eine Vorlage kann nach ihrer Erledigung eine oder mehrere andere Vorlagen
erzeugen. Jede Folgeaufgabe bleibt ein eigenständiges natives Home-Assistant-
To-do. Pro Folgeaufgabe kann eine Verzögerung im Format `HH:MM:SS` hinterlegt
werden. So lassen sich beispielsweise diese Ketten abbilden:

`Waschmaschine starten` → `Wäsche aufhängen` → `Wäsche abnehmen`

Die im Aufgabeneditor verwendete JSON-Struktur lautet beispielsweise:

```json
[
  {
    "task_id": "waesche_abnehmen",
    "delay": "02:00:00"
  }
]
```

### Zustandsbasierte Aufgaben

Der Zeitplantyp **Bei Zustandswechsel** erzeugt bei jedem passenden Wechsel
eine Aufgabe. `for` verlangt, dass der Zielzustand eine bestimmte Zeit bestehen
bleibt. **Fällig nach** verschiebt die Fälligkeit relativ zum Ereignis,
**Cooldown** verhindert zu häufige Wiederholungen und **nicht erneut erzeugen,
solange offen** vermeidet Duplikate.

### Wiederholung nach Erledigung

Beim Zeitplantyp **Nach letzter Erledigung** wird die nächste Fälligkeit nicht
vom ursprünglichen Termin, sondern vom tatsächlichen Abschlusszeitpunkt
berechnet. Zusätzlich wird ein erster Fälligkeitstermin angegeben, damit die
Serie beginnen kann.

### Zuweisungsarten

Jede Aufgabenvorlage kann auf eine von vier Arten verteilt werden:

- **Fest**: Die ausgewählte Person bleibt immer zuständig.
- **Rotation**: Die Aufgabe wechselt bei jeder Erzeugung reihum zwischen den
  ausgewählten Personen. Die Position bleibt über Neustarts erhalten.
- **Fair**: Gewählt wird zunächst die Person mit den bislang wenigsten
  Zuweisungen. Bei Gleichstand entscheidet die geringere Zahl aktuell offener
  Aufgaben, danach die Reihenfolge im Editor.
- **Offen**: Das To-do wird als `[Offen] Aufgabe` angelegt. Alle ausgewählten
  Personen – oder bei leerer Auswahl alle – erhalten **Übernehmen**. Erst die
  Übernahme setzt den Personennamen, zählt die Zuweisung und startet die
  persönliche Bearbeitung.

Bestehende Vorlagen ohne Zuweisungsart werden weiterhin als **Fest** behandelt.

Mit **Nur an anwesende Personen zuweisen** werden Kandidaten anhand ihrer
konfigurierten Anwesenheitsentität gefiltert. Ist niemand verfügbar, bleibt die
Aufgabe offen und wird automatisch zugewiesen, sobald eine geeignete Person
nach Hause kommt. Die gespeicherte Zuweisungsbegründung weist diesen Fall aus.

### Haushaltsübergabe

Im Bereich **Personen** kann die Verantwortung einer Person zeitlich begrenzt
an eine Vertretung übergeben werden. Bereits offene Aufgaben werden sofort
umgehängt; neue feste, rotierende und faire Zuweisungen folgen der aktiven
Übergabekette. Grund, Start, Ende und jede tatsächliche Neuzuweisung bleiben
für Auswertung und Audit nachvollziehbar.

Automationen können dafür `household_tasks.set_handover` mit `from_person`,
`to_person`, optional `until` und `reason` verwenden.
`household_tasks.clear_handover` beendet die Übergabe vorzeitig.

Bekannte Aufgaben eines Tages werden beim ersten Lauf nach dem Tageswechsel
angelegt. Die konfigurierte Uhrzeit ist ihre Fälligkeit und der Zeitpunkt der
ersten Benachrichtigungsstufe.

## NFC-Tags

Jede Aufgabenvorlage kann optional mit einer Home-Assistant-Tag-ID verbunden
werden. Die ID erscheint nach dem Scannen unter **Einstellungen > Tags** und
wird im Aufgabeneditor eingetragen. Drei Aktionen stehen zur Verfügung:

- **Erzeugen oder erledigen**: Der erste Scan erzeugt die Aufgabe. Gibt es
  bereits eine offene Aufgabe dieser Vorlage, erledigt der nächste Scan die
  älteste offene Aufgabe.
- **Nur erzeugen**: Jeder Scan erzeugt eine neue Aufgabe.
- **Nur erledigen**: Ein Scan erledigt die älteste offene Aufgabe, erzeugt aber
  keine neue.

Deaktivierte Vorlagen reagieren nicht auf NFC-Scans. Eine Tag-ID darf nur einer
Vorlage zugewiesen werden. Nach jeder Verarbeitung wird zusätzlich das Ereignis
`household_tasks_nfc_action` mit `tag_id`, `task_id`, `occurrence_id` und
`result` ausgelöst.

### Scan-Feedback

Unter **Einstellungen > NFC-Feedback** kann ein Administrator Push-
Bestätigungen deaktivieren, immer versenden oder auf Probleme beschränken.
Mögliche Empfänger sind die scannende Person, die zuständige Person oder beide.

Die scannende Person wird bevorzugt über die im Personenprofil hinterlegte
Home-Assistant-Benutzer-ID erkannt. Falls ein Scanner keinen Benutzerkontext
liefert, kann zusätzlich seine `device_id` als **NFC-Geräte-ID** im
Personeneditor eingetragen werden. Die ID ist beispielsweise in den Daten eines
`tag_scanned`-Ereignisses unter **Entwicklerwerkzeuge > Ereignisse** sichtbar.
Kann der Scanner nicht zugeordnet werden und ist ausschließlich **Scanner** als
Empfänger ausgewählt, wird keine Push-Bestätigung verschickt.

## Schnellaufgaben

Über **+ Schnellaufgabe** lässt sich eine einmalige Aufgabe anlegen. Dabei
werden Person, Fälligkeit, optionale Notiz und die Erinnerungsart gewählt:

- globale Standardregeln
- individuelle Eskalationszeiten
- keine Push-Erinnerungen

Die Aufgabe erscheint sofort als natives To-do, erzeugt aber keine dauerhafte
Vorlage. Eine hinterlegte Home-Assistant-Benutzer-ID sorgt dafür, dass im
Dialog automatisch die eigene Person als **(Ich)** vorausgewählt wird.

## Eskalationen

Die Standardregeln sind:

1. erster Hinweis zur Fälligkeit an die zuständige Person, auf Wunsch erst bei
   Anwesenheit;
2. Übergabe an die nächste geeignete Person zwei Stunden nach der ersten
   zugestellten Nachricht;
3. nach 24 Stunden Hinweis an alle.

Jede Stufe kann erinnern, an die nächste Person delegieren oder die Aufgabe zur
freien Übernahme öffnen. Jede Aufgabenvorlage und jede Schnellaufgabe kann die
globalen Regeln überschreiben.

## Punktestand

Jede manuell erledigte Aufgabe zählt einen Punkt. Das Familien-Ranking auf der
Heute-Seite zeigt den dauerhaften Gesamtstand und die im aktuellen Monat
erledigten Aufgaben.

Bei **Erledigt** aus einer Push-Nachricht wird der Punkt der Person zugerechnet,
die den Button erhalten und gedrückt hat. Bei einem Abschluss im Panel wird die
hinterlegte Home-Assistant-Benutzer-ID verwendet. Wird ein To-do direkt in der
nativen Liste erledigt und Home Assistant liefert keinen Benutzerkontext, zählt
der Punkt für die aktuell zuständige Person. Automatisch als behoben erkannte
Druckerprobleme zählen nicht.

## Auswertung

Die Seite **Auswertung** zeigt für die letzten 30 Tage erledigte Aufgaben,
Pünktlichkeitsquote, durchschnittliche Verspätung und die aktuelle offene
Arbeitslast pro Person. Zusätzlich werden die am häufigsten erledigten
Aufgabenvorlagen dargestellt. Automatisch als behoben erkannte
Druckerprobleme fließen nicht in die Abschlussstatistik ein.

Die **Haushalts-Retrospektive** erkennt wiederkehrend verspätete Vorlagen,
mehrfache Rückstände, ungleich verteilte offene Arbeit und auffällig viele
Übergaben. Sie zeigt konkrete Hinweise statt einer undurchsichtigen
Gesamtpunktzahl. Der Wochenabschluss enthält zusätzlich die Zahl aktueller
Retrospektiv-Hinweise.

Bei jeder erzeugten Aufgabe speichert die Integration außerdem die
Entscheidungsgrundlage ihrer Zuweisung. **Warum wurde mir das zugewiesen?**
zeigt je nach Modus die feste Zuordnung, Rotationsposition, damalige
Zuweisungszahl und offene Last oder eine spätere Übernahme beziehungsweise
Weitergabe.

## Wochenabschluss

Unter **Einstellungen > Wochenabschluss** kann ein wöchentlicher Push an alle
konfigurierten Personen aktiviert werden. Wochentag und Uhrzeit sind frei
wählbar. Die Nachricht fasst Erledigungen der letzten sieben Tage, aktuell
offene und überfällige Aufgaben sowie die Pünktlichkeitsquote zusammen. Pro
Kalenderwoche wird höchstens eine Zusammenfassung versendet.

## Konfiguration sichern

Administratoren können unter **Einstellungen > Konfiguration sichern** ein
versioniertes JSON-Dokument exportieren und wieder importieren. Es enthält
Personen, Aufgabenvorlagen, Standardregeln und Monitore. Laufende To-dos,
Verlauf, Punktestand und Rotationspositionen bleiben beim Import unverändert.
Ein Import wird vollständig validiert, bevor er die vorhandene Konfiguration
ersetzt.

### Aktionen in Push-Nachrichten

Auf dem iPhone stehen für jede Erinnerung diese Aktionen zur Verfügung:

- **Erledigt**: schließt das native Home-Assistant-To-do ab.
- **Heute Abend**: verschiebt Aufgabe und nächste Erinnerung auf 18 Uhr. Ist
  18 Uhr bereits vorbei, wird sie um zwei Stunden verschoben.
- **Morgen**: verschiebt Aufgabe und nächste Erinnerung auf morgen um 9 Uhr.
- **Kann ich nicht**: reicht die Aufgabe an eine andere, bevorzugt anwesende
  Person weiter. Bereits verwendete Personen werden bei der Rotation zunächst
  übersprungen.
- **Hilfe benötigt**: die Zuständigkeit bleibt bestehen, aber alle anderen
  Personen erhalten sofort einen Hilferuf mit **Ich helfe**. Nach einer Zusage
  erfährt die zuständige Person, wer unterstützt.

Ein Tipp auf die Nachricht selbst öffnet weiterhin die offizielle
Home-Assistant-To-do-Ansicht.

## Druckerprobleme

Unter **Einstellungen > Druckerprobleme** kann ein Administrator die
Überwachung aktivieren und eine zuständige Person auswählen. Die Integration
erkennt vorhandene primäre IPP-Statussensoren automatisch; Geräte- oder
Entitäts-IDs werden nicht in der Ausgangskonfiguration hinterlegt.

Ein von IPP gemeldeter Stopp, Fehler, Papierstau oder anderer konkreter
Fehlergrund erzeugt genau eine Aufgabe. Wiederholte identische Statusmeldungen
erzeugen keine Duplikate. Meldet der Drucker anschließend wieder einen gesunden
Zustand, wird die zugehörige Aufgabe automatisch abgeschlossen. Ein bloß
`unavailable` gemeldeter, ausgeschalteter oder schlafender Drucker erzeugt
bewusst keine Störung.

## Ressourcen und Verbrauch

Unter **Einstellungen > Ressourcen und Verbrauch** lassen sich generische
Sensorregeln als JSON konfigurieren. Jede Regel besitzt mindestens
`entity_id`, `condition`, `threshold`, `task_name` und `assignee`.

```json
{
  "softener_salt": {
    "enabled": true,
    "entity_id": "sensor.softener_salt_percent",
    "condition": "below",
    "threshold": 20,
    "task_name": "Salz der Enthärtungsanlage auffüllen",
    "description": "Aktueller Füllstand: {state} {unit}",
    "assignee": "person_a",
    "due_after": "24:00:00",
    "cooldown": "168:00:00",
    "auto_resolve": true
  }
}
```

Unterstützte Bedingungen sind `below`, `at_most`, `above`, `at_least`,
`equals` und `not_equals`. `{state}` und `{unit}` können in Name und
Beschreibung verwendet werden. `unknown` oder `unavailable` erzeugen und
schließen bewusst keine Aufgabe. Solange eine Störung aktiv ist, entsteht
höchstens ein Vorkommen; Cooldown und automatische Erholung verhindern
Benachrichtigungsfluten.

## Trockner

Der Zeitplantyp **Nach Gerätestatus** kann mehrere Trockner-Entitäten
überwachen. Sobald mindestens ein konfigurierter Trockner an einem Tag fertig
wurde, wird einmalig eine Aufgabe für die gewählte Person erzeugt. Vor der
konfigurierten Fälligkeit gilt sie am selben Tag, danach am Folgetag.

## Datenspeicherung

Die Integration verwendet den internen Home-Assistant-Speicher. Dateien unter
`.storage` niemals von Hand bearbeiten. Eine Home-Assistant-Sicherung enthält
sowohl Konfiguration als auch Aufgabenstatus.

Die mitgelieferte Ausgangskonfiguration enthält keine Namen, Benutzer-IDs,
Geräte-, Personen- oder Benachrichtigungsentitäten. Eine neue Installation
startet mit leeren Personen und Aufgabenvorlagen. Personenbezogene Daten
entstehen ausschließlich durch Eingaben in der lokalen UI.

**Auf Ausgangswerte zurücksetzen** verwirft angepasste Personen, Vorlagen und
Standardregeln und stellt den leeren, personenbezogen neutralen Zustand wieder
her. Bereits erzeugte To-dos und ihr Verlauf bleiben erhalten.
