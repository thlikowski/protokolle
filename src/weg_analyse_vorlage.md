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

<!-- Hier die ausgefüllten Blöcke einfügen -->

=== PROTOKOLL: _____________________________ ===

Dateiname:         
Objekt:            
Hausverwaltung:    
Datum Versammlung: 
Anzahl TOPs gesamt:   
Anzahl mit Beschluss: 

### TOP-Bezeichner
Typ: 
Exaktes Beispiel:
```

```
Zeile davor:  
Zeile danach: 

### Unter-TOPs
Vorhanden: 
Typ falls ja: 
Exaktes Beispiel:
```

```

### Beschlusstext-Beginn
- [ ] Direkt nach TOP-Zeile
- [ ] Nach Leerzeile
- [ ] Nach festem Marker: 
Beispiel-Anfang:
```

```

### Beschluss-Ende
Typ: 
Exakter Ende-Marker:
```

```
Zeile vor Ende:  
Zeile danach:    

### Abstimmungsergebnis
Typ: 
Exaktes Beispiel:
```

```
Position: 

### OCR-Qualität
- [ ] Gut
- [ ] Mittel
- [ ] Schlecht
Auffälligkeiten: 

### Sonderfälle / Besonderheiten


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
