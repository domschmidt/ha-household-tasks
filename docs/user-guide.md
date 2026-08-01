# Household Tasks

Die Integration verwaltet eigene, versionierte Aufgaben, verschickt
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
5. **Household Tasks** bestätigen; eine externe Aufgabenliste ist nicht nötig.
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

Administratoren und Benutzer, die in Household Tasks mit einer Person verknüpft
sind, können Aufgaben erledigen, bearbeiten, übernehmen, Schnellaufgaben
hinzufügen und aktive Vorlagen sofort auslösen. Nur Administratoren dürfen
Personen, Vorlagen und Regeln verändern.
Jedes Konfigurationsfeld besitzt einen direkt zugeordneten Hilfetext. Entitäten,
Benutzer, Geräte, Benachrichtigungsaktionen, Kalender und NFC-Tags werden aus
Home Assistant vorgeschlagen, damit keine technischen IDs blind eingegeben
werden müssen.

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
erzeugen. Jede Folgeaufgabe bleibt eine eigenständige Household-Tasks-Aufgabe.
Pro Folgeaufgabe kann eine Verzögerung im Format `HH:MM:SS` hinterlegt
werden. So lassen sich beispielsweise diese Ketten abbilden:

`Waschmaschine starten` → `Wäsche aufhängen` → `Wäsche abnehmen`

Vorhandene Vorlagen werden im Editor ausgewählt. Fehlt die gewünschte
Folgevorlage, kann sie dort direkt angelegt und ausgewählt werden, ohne den
aktuellen Entwurf zu verlassen.

Zusätzlich können Vorlagen im Expertenbereich echte Voraussetzungen wählen.
Eine daraus erzeugte Aufgabe erhält den Status **Blockiert**, solange eine
offene Aufgabe der Voraussetzung existiert. Nach deren Abschluss oder Abbruch
wird sie automatisch **Offen**. Der Gesundheitscheck verhindert fehlende oder
zyklische Verweise.

### Status, Checklisten und Aufgabenakte

Jede Aufgabe besitzt einen klaren Lebenszyklus: **Offen**, **In Arbeit**,
**Wartet**, **Blockiert**, **Erledigt** oder **Abgebrochen**. Das Kontextmenü der
Aufgabenkarte ändert den Status. Veraltete Browserstände können dank der
Revisionsnummer keine neueren Änderungen unbemerkt überschreiben.

Eine Vorlage kann eine Checkliste mit einem Schritt pro Zeile enthalten. Jeder
Schritt wird direkt auf der Aufgabenkarte abgehakt und speichert Zeitpunkt und
handelnde Person. Standardmäßig bleibt **Erledigt** deaktiviert, bis alle
Schritte abgeschlossen sind; diese Pflicht lässt sich pro Vorlage abschalten.

**Verlauf anzeigen** öffnet die Aufgabenakte mit Erstellung, Statuswechseln,
Checklistenfortschritt, Übernahmen und Abschluss. Der lokale Ereignisverlauf ist
auf 2.000 Einträge begrenzt.

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
- **Offen**: Die Aufgabe wird ohne zuständige Person angelegt. Alle ausgewählten
  Personen – oder bei leerer Auswahl alle – erhalten **Übernehmen**. Erst die
  Übernahme setzt den Personennamen, zählt die Zuweisung und startet die
  persönliche Bearbeitung.

Bestehende Vorlagen ohne Zuweisungsart werden weiterhin als **Fest** behandelt.

Mit **Anwesenheit bei der Zuweisung berücksichtigen** werden Kandidaten anhand
ihrer konfigurierten `person.*`-, `device_tracker.*`- oder
`binary_sensor.*`-Entität gefiltert. Bei einer festen Zuständigkeit gilt ohne
weitere Auswahl die sichere Regel **Warten, bis die Person zurück ist**. Eine
Aufgabe wird also nie mehr stillschweigend an irgendein Haushaltsmitglied
umverteilt.

Für feste Zuständigkeiten stehen vier Abwesenheitsregeln zur Verfügung:

- **Warten**: Die Aufgabe bleibt wartend und wird der zuständigen Person bei
  ihrer Rückkehr zugewiesen.
- **Ersatzperson**: Nur ausdrücklich ausgewählte, anwesende Ersatzpersonen
  kommen infrage; unter mehreren wird fair oder rotierend gewählt.
- **Zur Übernahme öffnen**: Nur die ausgewählten, anwesenden Ersatzpersonen
  können die Aufgabe übernehmen.
- **Trotzdem fest zuweisen**: Die ursprüngliche Person bleibt auch unterwegs
  zuständig.

Sind keine erlaubten Ersatzpersonen zuhause, wartet die Aufgabe. Die
gespeicherte Zuweisungsbegründung zeigt ursprüngliche Zuständigkeit,
Abwesenheitsregel, Kandidaten und das konkrete Auswahlergebnis an.

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

Die Aufgabe erscheint sofort im eigenen Aufgabenspeicher, erzeugt aber keine dauerhafte
Vorlage. Eine hinterlegte Home-Assistant-Benutzer-ID sorgt dafür, dass im
Dialog automatisch die eigene Person als **(Ich)** vorausgewählt wird.

## Eskalationen

Die Standardregeln sind:

1. erster Hinweis zur Fälligkeit an die zuständige Person, auf Wunsch erst bei
   Anwesenheit;
2. Übergabe an die nächste geeignete Person zwei Stunden nach der ersten
   zugestellten Nachricht;
3. nach 24 Stunden Hinweis an alle.

Die Oberfläche ist nicht auf drei Stufen begrenzt. Jede Stufe besitzt Dauer,
Bezugspunkt, Empfänger, Anwesenheitsregel und Aktion und kann erinnern, an die
nächste Person delegieren oder die Aufgabe zur freien Übernahme öffnen. Jede
Aufgabenvorlage und jede Schnellaufgabe kann die globalen Regeln überschreiben.

## Vorschau und Tests

Administratoren können Konfigurationen prüfen, bevor sie produktiv wirken:

- Aufgabenregeln zeigen die nächste berechnete Fälligkeit, passende
  Kalendertermine oder aktuelle Zustände der Auslöser.
- Kalenderregeln führen in drei Schritten durch Kalender, Suchmuster und
  zeitlichen Versatz. Mit **Kalendertitel für Aufgabenname verwenden** kann
  eine einzige Regel unterschiedliche Termine wie „Gelb“, „Bio“, „Schwarz“
  und „Blau“ als jeweilige Aufgabennamen übernehmen. Der Vorlagenname bleibt
  der Rückfallwert für Termine ohne Namen. Optionale Titelzuordnungen bilden
  reguläre Ausdrücke auf verständliche Aufgabennamen ab; die erste passende
  Zeile gewinnt. Mit **Nicht zugeordnete Termine ignorieren** werden irrelevante
  Einträge wie „Problemabfall“ sicher verworfen.

Beispiel für einen gemischten Abfallkalender:

| Titel-Muster (Regex) | Aufgabenname |
| --- | --- |
| `gelb\|gelber sack` | Gelbe Tonne rausstellen |
| `bio` | Biotonne rausstellen |
| `schwarz\|restmüll` | Schwarze Tonne rausstellen |
| `blau\|papier` | Blaue Tonne rausstellen |
- Ressourcenregeln vergleichen den aktuellen Sensorwert mit dem Grenzwert,
  ohne eine Aufgabe zu erzeugen.
- Personen zeigen den aktuellen Anwesenheitszustand und können eine ausdrücklich
  ausgelöste Testbenachrichtigung erhalten.
- NFC-Zuordnungen zeigen, ob der Tag in Home Assistant registriert ist und wann
  er zuletzt gescannt wurde.

Diese Vorschauen verändern weder Aufgaben noch Verlauf oder Punktestand. Nur die
Testbenachrichtigung sendet bewusst eine Nachricht.

### Wettervorhersage, Verteilung je Person und Saisonsperren

Der Zeitplantyp **Wettervorhersage** wertet die von einer vorhandenen
`weather.*`-Entität bereitgestellten täglichen oder stündlichen Vorhersagen aus.
Die Integration ruft dafür ausschließlich den lokalen Home-Assistant-Dienst
`weather.get_forecasts` auf und kontaktiert selbst keinen Wetteranbieter.

Für „Frostschutz beim eigenen Auto prüfen“ wird empfohlen:

- Zuweisung **Je Person eine Aufgabe** und Auswahl aller Personen mit eigenem
  Auto,
- tägliche Vorhersage mit 48 Stunden Prüfzeitraum,
- ein Tag Vorlauf und Bereitstellung um 18 Uhr,
- Attribut `templow`, Vergleich **kleiner als**, Grenzwert `0`,
- Saisonmonate Oktober bis März und
- **Nur einmal je Saison und Zielperson**.

Jede ausgewählte Person erhält eine eigene Aufgabe und kann sie unabhängig
erledigen. Die Saisonsperre gehört weiterhin zur ursprünglichen Zielperson,
auch wenn eine aktive Haushaltsübergabe die Aufgabe vorübergehend jemand
anderem zuweist. Die Wintersaison über den Jahreswechsel erhält einen
gemeinsamen Schlüssel, beispielsweise `2026-2027`.

**Regel testen / nächste Fälligkeit** zeigt ohne Nebenwirkungen:

- den ersten passenden Vorhersagetag und die geplante Aktivierungszeit,
- jeden geprüften Wert und das Ergebnis seiner Bedingung,
- Haushaltsmodus und Saisonentscheidung,
- alle entstehenden personenbezogenen Aufgaben sowie
- bereits vorhandene Saisonsperren mit Begründung.

In den Wetterbedingungen können optionale Testwerte und ein Testdatum
eingetragen werden. Dadurch lässt sich beispielsweise `templow = -3`
simulieren, ohne Live-Vorhersagen zu verwenden oder Aufgaben anzulegen.
Erfolgreiche Laufzeitprüfungen speichern denselben Entscheidungsverlauf für
**Warum nicht?**. Da Home Assistant vergangene Wettervorhersagen nicht
zuverlässig als Historie garantiert, verwendet die Anwendung bewusst
reproduzierbare Szenariotests statt eines irreführenden Forecast-Backtests.

**Saisonsperren zurücksetzen** ermöglicht einen kontrollierten erneuten Lauf
der Regel. Das Zurücksetzen wird in den Rückgängig-Stapel aufgenommen.

## Punktestand

Jede manuell erledigte Aufgabe zählt einen Punkt. Das Familien-Ranking auf der
Heute-Seite zeigt den dauerhaften Gesamtstand und die im aktuellen Monat
erledigten Aufgaben.

Bei **Erledigt** aus einer Push-Nachricht wird der Punkt der Person zugerechnet,
die den Button erhalten und gedrückt hat. Bei einem Abschluss im Panel wird die
hinterlegte Home-Assistant-Benutzer-ID verwendet. Automatisch als behoben erkannte
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
Personen, Aufgabenvorlagen, Standardregeln und Monitore. Laufende Aufgaben,
Verlauf, Punktestand und Rotationspositionen bleiben beim Import unverändert.
Ein Import wird vollständig validiert, bevor er die vorhandene Konfiguration
ersetzt.

### Aktionen in Push-Nachrichten

Auf dem iPhone stehen für jede Erinnerung diese Aktionen zur Verfügung:

- **Erledigt**: schließt die Household-Tasks-Aufgabe ab.
- **Heute Abend**: verschiebt Aufgabe und nächste Erinnerung auf 18 Uhr. Ist
  18 Uhr bereits vorbei, wird sie um zwei Stunden verschoben.
- **Morgen**: verschiebt Aufgabe und nächste Erinnerung auf morgen um 9 Uhr.
- **Kann ich nicht**: reicht die Aufgabe an eine andere, bevorzugt anwesende
  Person weiter. Bereits verwendete Personen werden bei der Rotation zunächst
  übersprungen.
- **Hilfe benötigt**: die Zuständigkeit bleibt bestehen, aber alle anderen
  Personen erhalten sofort einen Hilferuf mit **Ich helfe**. Nach einer Zusage
  erfährt die zuständige Person, wer unterstützt.

Ein Tipp auf die Nachricht selbst öffnet die Household-Tasks-Seitenleiste.

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
Sensorregeln über einen visuellen Editor konfigurieren. Sensor, Vergleich,
Grenzwert, Aufgabe, Zuständigkeit, Fälligkeit, Cooldown und automatische
Erholung sind eigene, erklärte Felder. Der Button **Aktuellen Wert prüfen**
zeigt sofort, ob die Regel mit dem aktuellen Sensorzustand auslösen würde.

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

Die Integration verwendet Home Assistants atomaren internen Speicher als
einzige Datenquelle. Dateien unter `.storage` niemals von Hand bearbeiten. Eine
Home-Assistant-Sicherung enthält Konfiguration, Aufgabenstatus, Checklisten und
den begrenzten Ereignisverlauf. Beim Upgrade werden ältere, zuvor gespiegelte
Aufgaben automatisch und idempotent in das native Schema migriert; externe
Listen-IDs werden danach nicht mehr verwendet.

Die mitgelieferte Ausgangskonfiguration enthält keine Namen, Benutzer-IDs,
Geräte-, Personen- oder Benachrichtigungsentitäten. Eine neue Installation
startet mit leeren Personen und Aufgabenvorlagen. Personenbezogene Daten
entstehen ausschließlich durch Eingaben in der lokalen UI.

**Auf Ausgangswerte zurücksetzen** verwirft angepasste Personen, Vorlagen und
Standardregeln und stellt den leeren, personenbezogen neutralen Zustand wieder
her. Bereits erzeugte Aufgaben und ihr Verlauf bleiben erhalten.

## Komfort- und Betriebsfunktionen

### Einrichtungsassistent und Vorlagengalerie

Eine leere Installation startet mit einem Einrichtungsassistenten. Er legt die
erste Person und eine ausgewählte Starter-Vorlage gemeinsam an und zeigt davor
eine Vorschau von Zeitplan, Priorität und Punkten. Weitere kuratierte Vorlagen
für Frostschutz, Pollenfilter, Reifenwechsel, Gäste und den Wochenabschluss
stehen unter **Aufgaben > Vorlagengalerie** bereit. Zustandsbasierte Vorlagen
verlangen dabei ausdrücklich eine vorhandene Home-Assistant-Entität.

Das Aufgabenformular zeigt zunächst nur die üblichen Felder. Aufgabenmarkt,
Saison, NFC, Folgeaufgaben und eigene Eskalationen liegen in den aufklappbaren
Expertenoptionen. **Regel testen** zeigt nicht nur die nächste Fälligkeit,
sondern auch, ob Haushaltsmodus und Saison die Erzeugung aktuell zulassen.

### Urlaub, Gäste und saisonale Aufgaben

Unter **Einstellungen > Urlaubs- und Gastmodus** stehen drei Betriebsarten zur
Verfügung:

- **Normal** führt alle regulären Regeln aus.
- **Urlaub** pausiert automatische Aufgaben, reduziert sie auf hohe
  Prioritäten oder delegiert sie an eine Vertretung. Jede Vorlage kann dieses
  Verhalten überschreiben.
- **Gäste** aktiviert Gastaufgaben und kann dafür ungeeignete private Routinen
  auslassen.

Ein optionales Ende schaltet den Haushalt automatisch auf Normalbetrieb
zurück. Saisonale Vorlagen können zusätzlich auf Monate und einen Sensorwert
begrenzt werden, etwa Frostwarnung unter 2 °C oder einen Pollenstatus `high`.
Manuell gestartete Aufgaben bleiben bewusst möglich.

### Aufgabenmarkt und gegenseitige Hilfe

Offene Aufgaben können von berechtigten Personen übernommen werden. Jede
Vorlage kann Priorität, Punkte und eine sichtbare Belohnung tragen. Bei
**Hilfe anfordern** bleibt die Zuständigkeit erhalten; andere Personen erhalten
eine Aktions-Benachrichtigung zur freiwilligen Unterstützung. **Heute nicht
geschafft** versucht zuerst eine geeignete Weitergabe und öffnet die Aufgabe
ansonsten zur Übernahme, bevor die normale Eskalation greift.

Push-Nachrichten und das Panel bieten dieselben Kernaktionen: **Erledigt**,
**Heute Abend**, **Morgen**, **Übernehmen** und **Hilfe anfordern**.

### Suche, Erklärungen und Rückgängig

**Strg/⌘ + K** öffnet die globale Suche über Vorlagen, Personen, offene und
erledigte Aufgaben, NFC-Tags sowie Home-Assistant-Entitäten. Entitäten und
Tag-IDs lassen sich direkt kopieren.

**Warum nicht?** erklärt den aktiven Haushaltsmodus, Saisonbedingungen,
geeignete Personen, ausgeschlossene Personen und zuletzt ausgelassene
Erzeugungsversuche. **Warum wurde mir das zugewiesen?** bleibt die ergänzende
Erklärung für bereits erzeugte Aufgaben.

Nach Erledigen, Löschen, Übergaben, Importen und Modusänderungen erscheint im
Kopf des Panels **Rückgängig**. Der Verlauf ist absichtlich auf die letzten
20 lokalen Aktionen begrenzt.

### Gesundheitscheck und Mobilansicht

Der Gesundheitscheck unter **Einstellungen** meldet beschädigte Statuswerte,
verwaiste Abhängigkeiten, doppelte Checklisten-IDs, nicht verfügbare
Anwesenheits- oder Saisonentitäten, ungültige
Benachrichtigungsdienste, nicht registrierte NFC-Tags und zyklische
Folgeaufgaben. Auf schmalen Displays zeigt **Jetzt sinnvoll** bis zu drei
Aktionen, gewichtet nach eigener Zuständigkeit, Priorität und Fälligkeit.

## Persönlicher Arbeitsbereich und Wochenplanung

Jeder Hauptbereich besitzt eine direkt teilbare URL. Der Parameter `view`
bleibt beim Neuladen erhalten und unterstützt auch die Zurück-/Vorwärts-Tasten
des Browsers:

| Bereich | URL |
| --- | --- |
| Heute | `/haushaltsaufgaben?view=today` |
| Meine Aufgaben | `/haushaltsaufgaben?view=mine` |
| Wochenplan | `/haushaltsaufgaben?view=week` |
| Aufgaben | `/haushaltsaufgaben?view=tasks` |
| Personen | `/haushaltsaufgaben?view=people` |
| Auswertung | `/haushaltsaufgaben?view=analytics` |
| Verlauf | `/haushaltsaufgaben?view=history` |
| Einstellungen | `/haushaltsaufgaben?view=settings` |

Unbekannte `view`-Werte öffnen sicher die Heute-Ansicht. Aufgabenbezogene
Push-Nachrichten und persönliche iOS-Widgets verlinken direkt auf
**Meine Aufgaben**.

**Meine Aufgaben** filtert automatisch auf die mit dem aktuellen
Home-Assistant-Benutzer verknüpfte Person. Neben direkt zugewiesenen Aufgaben
erscheinen dort angenommene Hilfen und passende offene Aufgaben. Häufig
verwendete Vorlagen können als Favoriten gespeichert und anschließend mit
einem Tipp erzeugt werden.

In **Meine Aufgaben** und im **Wochenplan** lassen sich mehrere Vorkommen
auswählen und gemeinsam erledigen, auf morgen verschieben oder als Hilferuf
verteilen. Der Wochenplan gruppiert die nächsten sieben Tage und zeigt dadurch
Überlastungen frühzeitig. Noch nicht erzeugte Aufgaben aus wöchentlichen,
monatlichen, jährlichen, mehrmonatigen und kalendergestützten Zeitplänen
erscheinen dort als gestrichelte **Vorschau**. Kalendertermine berücksichtigen
dabei bereits den zeitlichen Versatz, das Suchmuster und die geordnete
Regex-Titelzuordnung; nicht zugeordnete Termine bleiben wie konfiguriert
ausgeblendet. Diese Einträge sind bewusst schreibgeschützt:
Zuweisung, Bedingungen und offene Vorgänger werden erst bei der tatsächlichen
Erzeugung abschließend ausgewertet. Bereits erzeugte Aufgaben ersetzen ihre
Vorschau automatisch und können weiterhin ausgewählt oder verschoben werden.
Der schwebende Plus-Button öffnet abhängig von der
aktuellen Ansicht eine Schnellaufgabe, neue Vorlage oder Person.

## Smarte Schnellerfassung

**Smart erfassen** versteht kompakte deutsche Eingaben wie:

```text
Müll morgen 18 Uhr an Alex, dringend, 2 Punkte
```

Name, Person, Termin, Priorität und Punkte werden ausschließlich lokal
extrahiert und zunächst in die normalen Formularfelder übernommen. Erst ein
weiterer Klick auf **Aufgabe hinzufügen** erzeugt die Aufgabe. Unklare Werte
bleiben sichtbar und können korrigiert werden.

## Autodiscovery und aktionsfähige Diagnose

Der Bereich **Home-Assistant-Autodiscovery** erkennt lokal typische
Batteriesensoren, Haushaltsgeräte, Verbrauchs- und Wartungssensoren sowie
Abfallkalender. Vorschläge werden nie automatisch aktiviert. **Einrichten**
zeigt Entität, eigene Regel-ID und Zuständigkeit, bevor eine Aufgabe oder
Ressourcenregel gespeichert wird.

Gesundheitshinweise besitzen, soweit eindeutig möglich, **Beheben**. Der Button
öffnet direkt die betroffene Person, Vorlage oder Integrationskonfiguration.
Automatische Reparaturen, die eine mehrdeutige Entitätsauswahl treffen müssten,
werden bewusst nicht ausgeführt.

## Gebündelte Benachrichtigungen

Unter **Einstellungen > Intelligente Benachrichtigungsbündelung** lassen sich
Routinehinweise bis zu einer täglichen Zustellzeit sammeln. Jede Person erhält
eine kompakte Nachricht mit bis zu drei direkten Erledigt-Aktionen. Kritische
Aufgaben, Hilferufe und offene Übernahmen werden weiterhin sofort versendet.

## Home Assistant Assist

Die Integration registriert die Intents `HouseholdTasksList`,
`HouseholdTasksComplete` und `HouseholdTasksCreate`. Damit kann Assist eigene
Aufgaben vorlesen, eine eindeutig passende Aufgabe erledigen und eine
Schnellaufgabe für eine konfigurierte Person anlegen.

Home Assistant lädt Formulierungen für benutzerdefinierte Integrationen nicht
automatisch in den globalen Sprachkatalog. Kopiere deshalb die gewünschte Datei
aus `examples/custom_sentences/de` oder `examples/custom_sentences/en` nach
`/config/custom_sentences/<sprache>/household_tasks.yaml` und lade Home
Assistant neu. Danach funktionieren beispielsweise:

- „Welche Haushaltsaufgaben habe ich heute?“
- „Markiere Müll rausbringen als erledigt.“
- „Erstelle die Aufgabe Pflanzen gießen für Alex.“

Bei mehrdeutigen Abschlüssen verändert Assist bewusst nichts.

## Komfortplanung und lokale Assistenz

Die Startseite passt ihren Schwerpunkt an die Tageszeit an: morgens steht die
Tagesplanung im Vordergrund, tagsüber die nächsten sinnvollen Aufgaben und
abends die Abendrunde. **Heute planen** öffnet alle nahen Aufgaben in einer
gemeinsamen Planungsansicht. Im Wochenplan lassen sich Aufgaben per
Drag-and-drop auf einen anderen Tag verschieben.

Über **Natürlich verschieben** kann eine Aufgabe beispielsweise auf „morgen 18
Uhr“, „am Wochenende“ oder „wenn Alex zuhause ist“ gelegt werden. Bei einer
Anwesenheitsbedingung wartet Household Tasks lokal auf die konfigurierte
Anwesenheitsentität und aktiviert die Aufgabe beim nächsten Scan.

Die smarte Schnellerfassung unterstützt außerdem mehrere Zeilen oder mit
Semikolon getrennte Aufgaben. Vor dem Anlegen wird für jede Zeile eine Vorschau
mit erkannter Person und Fälligkeit angezeigt.

## Gewohnheiten, Stapel und flexible Serien

Nach mindestens zwei Erledigungen einer Vorlage zeigt Household Tasks eine
transparente lokale Empfehlung für die typische Person und Uhrzeit. Es werden
keine Daten an einen externen Dienst übertragen. Empfehlungen werden erst nach
einer ausdrücklichen Bestätigung in die Vorlage übernommen.

Aufgabenstapel bündeln mehrere Vorlagen zu einer Routine wie „Abendrunde“. Ein
Start erzeugt alle enthaltenen Aufgaben in der festgelegten Reihenfolge.

Der Zeitplantyp **Flexibel nach Erledigung** besitzt drei Intervalle:
frühestens, bevorzugt und spätestens. Die nächste Aufgabe wird zum bevorzugten
Zeitpunkt fällig; das zulässige Fenster bleibt auf der Aufgabenkarte sichtbar.

## Kontextmenüs, Geräteakten und Anhänge

Ein Rechtsklick oder langes Drücken öffnet das Kontextmenü einer Aufgabe. Dort
stehen Verschieben, Hilfe, Delegation, Geräteakte und Anhänge zur Verfügung.

Eine Geräteakte kann eine Home-Assistant-Entität, Modell, Ersatzteil,
Handbuch-URL und Notizen enthalten. Sie zeigt außerdem den aktuellen
Entitätszustand und die letzten Erledigungen.

Fotos, WebP-Bilder und PDF-Belege können direkt an eine Aufgabe gehängt werden.
Sie werden im lokalen Home-Assistant-Speicher abgelegt. Pro Datei gelten 750 KB,
pro Aufgabe maximal zehn Anhänge. Die eigentlichen Dateiinhalte werden erst beim
Öffnen über die WebSocket-API übertragen.

## Fehlervermeidung und Offline-Bedienung

Vorlagen zeigen vor dem Speichern eine verständliche Mengenprognose. Regeln mit
mehr als ungefähr 14 erwarteten Erzeugungen pro Woche benötigen eine zusätzliche
Bestätigung. Der Gesundheitscheck erkennt außerdem ähnliche Vorlagennamen,
doppelte NFC-Tags, fehlende Geräteentitäten und bestehende
Abhängigkeitskreise.

Das Panel hält einen lokalen, inhaltsbegrenzten Snapshot für eine bereits
geöffnete mobile Ansicht vor. Erledigen, Verschieben und ausgewählte
Massenaktionen können bei fehlender Verbindung vorgemerkt und nach Rückkehr der
Verbindung synchronisiert werden. Dies ist keine eigenständige Offline-PWA:
Home Assistant muss für den erstmaligen Seitenaufruf erreichbar sein.

## Wetter- und Klimaregeln

Der Zeitplantyp **Wetterregel** erzeugt Aufgaben aus normalen Sensoren oder aus
Attributen einer `weather.*`-Entität. Dadurch sind sowohl einfache Regeln als
auch kombinierte Bedingungen möglich:

- `sensor.aussentemperatur` ist kleiner als `2`
- `weather.home.temperature` ist größer als `28`
- `weather.home.wind_speed` ist größer als `60`
- `weather.home.precipitation_probability` ist mindestens `70`
- Wetterzustand ist gleich `snowy`

Mehrere Bedingungen lassen sich mit **UND** oder **ODER** verbinden. Eine
Glatteisregel kann beispielsweise verlangen, dass die Temperatur unter 1 °C
liegt **und** die Niederschlagswahrscheinlichkeit über 30 Prozent liegt. Die
Vorschau zeigt für jede Teilbedingung den aktuellen Wert und ob sie erfüllt ist.

`Fällig nach` verschiebt den Termin relativ zum erkannten Wetterzustand.
`Cooldown` begrenzt wiederholte Erzeugungen. **Nicht erneut erzeugen, solange
offen** verhindert doppelte Aufgaben bei länger anhaltendem Wetter. Ist eine
Wetterentität nicht verfügbar, wird sicherheitshalber keine Aufgabe erzeugt und
der Gesundheitscheck weist auf die fehlende Entität hin.

Die Vorlagengalerie enthält unter anderem Frostschutz, Garten-Hitzeschutz,
Sturmsicherung, Starkregen-/Fensterprüfung, Glatteisvorsorge, Lüften bei hoher
Feuchte, UV-Schutz, Schneeräumen und Hitzeschutz für Haustiere. Vorlagen werden
erst nach Auswahl einer konkreten lokalen Entität aktiviert.

## iPhone- und iPad-Widgets

Die empfohlene kostenlose iOS-Anbindung verwendet das offizielle
Home-Assistant-Custom-Widget. Pro Person werden ein Sensor mit der nächsten
Aufgabe, fünf stabile Sensoren für eine read-only Aufgabenliste, persönliche
Zählsensoren und ein Aktionsknopf angelegt. Die Listenplätze behalten ihre
Entity-IDs und zeigen jeweils den aktuell zugehörigen Aufgabentitel als Zustand.
Der Knopf sendet eine aktuelle,
aufgabenspezifische Benachrichtigung; deren Aktionen werden im Hintergrund
ausgeführt und öffnen weder Home Assistant noch Scriptable. Einrichtung,
Berechtigungen und optionale Aktualisierungsautomation sind unter
[`docs/ios-widget.md`](ios-widget.md) beschrieben. Die Anleitung enthält eine
Prüfliste für die erzeugten Entitäten, trennt App-Editor und iOS-Home-Screen
klar voneinander und gibt für alle vier Beispielbilder die exakte Reihenfolge,
Beschriftung, Symbole, Farben und Tap-Aktionen an. Die Bilder sind Designkonzepte;
Abweichungen durch das automatisch erzeugte, gleichmäßige iOS-Kachelraster sind
dort ausdrücklich dokumentiert.

Scriptable bleibt als optionale, flexiblere Anzeige verfügbar. Interaktive
Widget-Taps öffnen technisch bedingt jedoch immer die Scriptable-App.

### Scriptable

Unter `clients/scriptable` liegt ein eigenständiger Scriptable-Client. Er zeigt
offene, heute fällige, überfällige und blockierte Aufgaben als Home-Screen-
Widget. Ein Antippen öffnet das Aktionsmenü zum Erledigen, Übernehmen,
Verschieben, Starten, Bearbeiten der Checkliste oder Anfordern von Hilfe.

Der Client verwendet die versionierte Schnittstelle
`/api/household_tasks/v1/tasks` mit der normalen Home-Assistant-Bearer-
Authentifizierung. Empfohlen wird ein eigener Nicht-Administrator-Benutzer, der
in Household Tasks genau einer Person zugeordnet ist. Ein Administrator mit
mehreren Personen gibt beim Setup zusätzlich die technische Person-ID an.

Der langlebige Zugriffstoken wird ausschließlich im iOS-Schlüsselbund
gespeichert. In den Offline-Cache gelangen nur die bereits auf die Person
reduzierten Aufgabendaten. Für Zugriffe außerhalb des lokalen Netzes sollte
Home Assistant über HTTPS, Home Assistant Cloud oder ein vertrauenswürdiges VPN
erreichbar sein. Die vollständige Installation und Fehlerhilfe steht in
`clients/scriptable/README.md`.
