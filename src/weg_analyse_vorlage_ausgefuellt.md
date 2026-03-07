# WEG Protokoll-Analyse – Strukturvorlage

> Ziel: Für jedes Protokoll dokumentieren WIE Beschlüsse bezeichnet und abgegrenzt sind.
> Basis für Regex- und LLM-Prompts.
> Ausgefüllt von: Thomas | Datum: ___________

---

## Legende

**TOP-Bezeichner-Typen** (bitte in den Blöcken unten angeben):
- `A` → `TOP 4:` oder `TOP 4 –`
- `B` → `zu TOP 4:` oder `Zu TOP 4`
- `C` → `Tagesordnungspunkt 4`
- `D` → `Zu 4. der Tagesordnung:`
- `E` → `4 ) Titel` (Nummer + Klammer)
- `F` → Anderes (bitte exakt angeben)

**Unter-TOP-Typen:**
- `U1` → `4.1`, `4.2` (Punkt-Notation)
- `U2` → `4 a)`, `4 b)` (Buchstabe)
- `U3` → Kein Bezeichner, implizit durch Einrückung/Kontext
- `U4` → Anderes

**Abstimmungs-Formate:**
- `V1` → `JA-Stimmen: X / NEIN-Stimmen: Y / Enthaltungen: Z`
- `V2` → `Ja: X, Nein: Y, Enthalten: Z`
- `V3` → `einstimmig angenommen`
- `V4` → `Der Beschluss wird mit X Ja-Stimmen ... angenommen`
- `V5` → Anderes

**Beschluss-Ende-Marker:**
- `E1` → Nächster TOP-Header beginnt
- `E2` → Leerzeile + fester Begriff (`Abstimmungsergebnis:`, `Es wird beschlossen:`)
- `E3` → Kein klarer Marker, Ende durch Kontext
- `E4` → Anderes

---

## Protokoll-Blöcke

<!-- VORLAGE – kopieren und ausfüllen für jedes Protokoll -->
<!--
=== PROTOKOLL: _____________________________ ===

Dateiname:         [z.B. Frauentor_2023-11-15_durchsuchbar.pdf]
Objekt:            [Frauentor / DrKuelzStr / Mariental / Rosengarten]
Hausverwaltung:    [La Casa / MM-Consult / Bernhardt]
Datum Versammlung: [TT.MM.JJJJ]
Anzahl TOPs gesamt:   ___
Anzahl mit Beschluss: ___

### TOP-Bezeichner
Typ: [A/B/C/D/E/F]
Exaktes Beispiel aus dem Dokument:
```
[Text hier einfügen]
```
Zeile davor:  [Text oder "Seitenende" / "Leerzeile"]
Zeile danach: [Text]

### Unter-TOPs
Vorhanden: [ja/nein]
Typ falls ja: [U1/U2/U3/U4]
Exaktes Beispiel:
```
[Text hier einfügen]
```

### Beschlusstext-Beginn
Wie beginnt der eigentliche Beschlusstext nach dem TOP-Titel?
- [ ] Direkt nach TOP-Zeile (kein Abstand)
- [ ] Nach Leerzeile
- [ ] Nach festem Marker (welcher?: _______________)
Beispiel-Anfang:
```
[Erste 2-3 Zeilen des Beschlusstexts]
```

### Beschluss-Ende
Typ: [E1/E2/E3/E4]
Exakter Ende-Marker falls vorhanden:
```
[Text hier einfügen]
```
Zeile vor Ende:  [Text]
Zeile nach Ende: [Text oder "neuer TOP"]

### Abstimmungsergebnis
Typ: [V1/V2/V3/V4/V5]
Exaktes Beispiel aus dem Dokument:
```
[Text hier einfügen]
```
Position: [innerhalb Beschlusstext / nach Beschlusstext / am TOP-Ende]

### OCR-Qualität
- [ ] Gut (kaum Fehler)
- [ ] Mittel (gelegentliche Fehler, Umlaute OK)
- [ ] Schlecht (viele Fehler, Struktur kaum erkennbar)
Auffälligkeiten:  [z.B. "Seitenumbruch mitten im Beschluss", "fehlende Leerzeichen"]

### Sonderfälle / Besonderheiten
[z.B. "TOP 6 hat keinen Beschluss, nur Diskussionsbericht"
      "Zwei Beschlüsse unter einem TOP ohne Unter-TOP-Bezeichner"
      "Abstimmung fehlt komplett"]

=== ENDE PROTOKOLL ===
-->

---

## Ausgefüllte Protokolle

=== PROTOKOLL: Rosengarten_2025-11-17_durchsuchbar.pdf ===

Dateiname:         Rosengarten_2025-11-17_durchsuchbar.pdf
Objekt:            Rosengarten
Hausverwaltung:    MM-Consult
Datum Versammlung: 17.11.2025
Anzahl TOPs gesamt:    10
Anzahl mit Beschluss:   8  (TOP 4.1–4.4, 5, 6, 7, 8; TOP 9 zurückgezogen; TOP 1/2/3/10 kein Beschluss)

### TOP-Bezeichner
Typ: E  (`X )` – Nummer + Leerzeichen + Klammer)

Exaktes Beispiel aus dem Dokument (wie pypdf liest, mit OCR-Artefakten):
```
5)  Beschlussfassung  zur  zeitbefristeten  V erlängerung  des  W ärmeliefervertr ages
6 ) Beschlussfassung  zum  Wirtschaftsplan  2025
7)  Anstrich  der  Holzbauteile  der  Balkonanlagen
8  ) Beschlussfassung  zur  Erhöhung  der  Zuführung  zur  Instandhaltungsrücklage
```
Zeile davor:  Leerzeile ODER Abstimmungsergebnis des vorherigen TOPs
Zeile danach: Beschreibungstext oder direkt „Beschluss:"

⚠️ OCR-Problem: Leerzeichen zwischen Zahl und `)` inkonsistent!
  - `5)` (kein Leerzeichen)
  - `6 )` (ein Leerzeichen)  
  - `8  )` (zwei Leerzeichen)
→ Regex muss `\d+\s*\)` matchen, NICHT `\d+\)`

### Unter-TOPs
Vorhanden: JA
Typ: Sonderfall – weder U1 noch U2 sauber!

Exaktes Beispiel aus dem Dokument (roher pypdf-Text):
```
4 1)  Beschlussfassung  zur  Jahr esabr echnung  2024
42)  Beschlussfassung  zur  Einzelabr echnung  2024
4  3)  Beschlussfassung  zur  Instandhaltungsrücklage
4  4)  Entlastung  der  V erwaltung
```
⚠️ KRITISCHES OCR-Problem: Im PDF steht `4 1)`, `4 2)`, `4 3)`, `4 4)`
   pypdf liest das inkonsistent:
   - `4 1)` → mit Leerzeichen ✓
   - `42)` → OHNE Leerzeichen (Leerzeichen verschluckt!) ✗
   - `4  3)` → zwei Leerzeichen
   - `4  4)` → zwei Leerzeichen
→ Das ist die bekannte Sub-TOP-Bug-Ursache! Nummer wird als "42" statt "4.2" erkannt.
→ Regex muss `(\d+)\s+(\d+)\s*\)` als Unter-TOP erkennen

### Beschlusstext-Beginn
- [ ] Direkt nach TOP-Zeile
- [ ] Nach Leerzeile
- [X] Nach festem Marker: `Beschluss:` (immer vorhanden, sehr zuverlässig!)

Beispiel-Anfang (roher Text):
```
Beschluss:  Die  Eigentümer gemeinschaft  beschließt  die  Gesamtabr echnung  des
Wirtschaftsjahr es  2024  in der  vorliegenden  Form
```
⚠️ OCR-Problem: Wörter werden mit Extra-Leerzeichen gerendert (`Eigentümer gemeinschaft`)
   und Wörter werden mitten getrennt (`Jahr esabr echnung`, `W ärmeliefervertr ages`)

### Beschluss-Ende
Typ: E2 – fester Marker `Ergebnis:` beendet den Beschlusstext zuverlässig

Exakter Ende-Marker:
```
Er gebnis:  Beschluss  angenommen  und  verkündet
```
⚠️ OCR-Problem: `Ergebnis` wird als `Er gebnis` gelesen (Leerzeichen mitten im Wort!)

Zeile vor Ende:  `Anzahl  Enthaltungen:  0,0000`
Zeile danach:    Leerzeile, dann nächster TOP-Bezeichner

Vollständige Abstimmungsblock-Struktur (immer gleich):
```
Anzahl  Ja  Stimmen:  10000,0000
Anzahl  Nein  Stimmen:  0,0000
Anzahl  Enthaltungen:  0,0000

Er gebnis:  Beschluss  angenommen  und  verkündet
```

### Abstimmungsergebnis
Typ: Sonderformat (keiner der Standardtypen V1–V4 passt exakt)

Exaktes Beispiel aus dem Dokument:
```
Anzahl  Ja  Stimmen:  10000,0000
Anzahl  Nein  Stimmen:  0,0000
Anzahl  Enthaltungen:  0,0000
Er gebnis:  Beschluss  angenommen  und  verkündet
```
Position: Nach Beschlusstext, vor nächstem TOP

Besonderheit: Stimmen als Miteigentumsanteile (MEA), nicht als Personenanzahl!
  `10000,0000` = 100% der MEA (Tausendstel-Anteile), nicht 10.000 Personen.

### OCR-Qualität
- [ ] Gut
- [X] Mittel (Struktur erkennbar, aber systematische Wort-Splitting-Fehler)
- [ ] Schlecht

Auffälligkeiten (systematisch, nicht zufällig!):
1. **Wort-Splitting**: `Eigentümer gemeinschaft`, `Jahr esabr echnung`, `W ärmeliefervertr ages`, `Er gebnis`, `V erwalter`, `W ohnung`
2. **Extra-Leerzeichen** zwischen allen Wörtern: `Die  Eigentümer gemeinschaft  beschließt`
3. **Inkonsistente Leerzeichen bei Unter-TOP-Nummern**: `4 1)` vs `42)` vs `4  3)`
4. **m² → m?**: Sonderzeichen-Problem: `2,21 € pro m? / Jahr` statt `m²`

### Sonderfälle / Besonderheiten
- TOP 4 ist ein Dach-TOP ohne eigenen Beschluss – nur die Unter-TOPs 4.1–4.4 haben Beschlüsse
- TOP 9 wurde durch Verwalter zurückgezogen → kein Beschluss, aber Text vorhanden
- TOP 10 (Sonstiges) hat einen Auftrag an den Verwalter, aber keine formelle Abstimmung
- Erster TOP der Unter-TOPs (`4 1)`) auf Seite 2, obwohl Dach-TOP `4)` auf Seite 1 endet

=== ENDE PROTOKOLL ===

---

## Zusammenfassung (nach Ausfüllen aller Protokolle)

| Dateiname | Objekt | TOP-Typ | Unter-TOP | Abstimmung-Typ | Ende-Marker | OCR-Qualität |
|-----------|--------|---------|-----------|----------------|-------------|--------------|
|           |        |         |           |                |             |              |

---

## Muster-Übersicht (nach Analyse)

### TOP-Bezeichner je Hausverwaltung
| HV | Typ | Beispiel |
|----|-----|---------|
| MM-Consult | | |
| La Casa | | |
| Bernhardt | | |

### Abstimmungs-Formate
| Format | Häufigkeit | Beispiel |
|--------|-----------|---------|
| | | |

### Häufigste OCR-Probleme
| Problem | Betroffene PDFs | Beispiel |
|---------|----------------|---------|
| | | |
