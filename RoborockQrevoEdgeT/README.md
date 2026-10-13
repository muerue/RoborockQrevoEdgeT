# Roborock Qrevo EdgeT für IP-Symcon

IP-Symcon-Gerätemodul für den Roborock Qrevo EdgeT (`roborock.vacuum.a197`, Firmware `02.21.90`), umgesetzt mit `python-roborock`. Es benötigt weder Home Assistant noch Xiaomi Mi Home.

> **Status dieses Pakets:** Die Modul- und Brücken-Dateien sind lokal syntaktisch geprüft. Ein Lauf gegen ein IP-Symcon-System bzw. einen echten Roboter kann in dieser Entwicklungsumgebung nicht ausgeführt werden. Deshalb ist das Paket als erste integrierbare Version zu verstehen; die Verbindung und die Gerätebefehle müssen im Zielsystem getestet werden.

## Funktionen

- Roborock-Login einmalig per E-Mail-Verifizierungscode und Speicherung der Sitzung auf dem Linux-System.
- Periodische Aktualisierung von Batterie, Status, Fehlercode, Fortschritt, Reinigungsfläche und Ladezustand.
- Export der aktuellen Karte als PNG und JSON; Anzeige des Kartenbildes als IP-Symcon-Medienobjekt.
- Raum-Metadaten, Konturen/Begrenzungen, Position, Ladestation sowie Sperr- und Wischsperrbereiche im JSON.
- Starten, Pausieren, Stoppen und Rückkehr zur Ladestation.
- Raumreinigung per Modul-Funktion mit Wiederholungswert 1–3. Vor dem Senden wird der Raum gegen aktuelle Karten-Metadaten geprüft. Unzugeordnete Segmente, insbesondere Segment 3 aus dem bisherigen Export, werden abgewiesen.

## Voraussetzungen

- IP-Symcon 9.x auf Linux.
- Die bereits funktionierende Python-Umgebung mit `python-roborock==7.12.3` (in der bisherigen Einrichtung: `/root/roborock-test/bin/python`).
- Das IP-Symcon-Dienstkonto muss Python, die Moduldateien sowie Session- und Cache-Dateien lesen bzw. schreiben können.
- PHP muss `proc_open` erlauben. Die Brücke wird über ein Argument-Array gestartet, nicht über eine Shell-Befehlszeichenkette.

## Paketstruktur

- `library.json`: Bibliotheksmanifest
- `RoborockQrevoEdgeT/module.json`: IP-Symcon-Moduldefinition
- `RoborockQrevoEdgeT/module.php`: Instanz, Variablen, Timer, Befehle und Medienobjekt
- `RoborockQrevoEdgeT/form.json`: Konfiguration
- `RoborockQrevoEdgeT/scripts/roborock_bridge.py`: JSON-Brücke, Login und Gerätebefehle
- `RoborockQrevoEdgeT/scripts/export_roborock_map.py`: Kartendaten-Normalisierung

## Installation der Bibliothek

Das ZIP enthält `library.json` und den Ordner `RoborockQrevoEdgeT`. Lege beide in das Wurzelverzeichnis deiner eigenen IP-Symcon-Modulbibliothek. Für die Standardinstallation über Module Control muss die Bibliothek über eine für dein Setup erreichbare Git-Repository-URL bereitgestellt und in Module Control eingetragen werden. Falls du bereits ein lokales Git-Modul-Repository verwendest, füge die Dateien dort hinzu, committe sie und aktualisiere das Repository in Module Control. Danach eine Instanz **Roborock Qrevo EdgeT** erstellen.

## Roborock-Anmeldung einmalig einrichten

Der Login fragt interaktiv nach E-Mail und Verifizierungscode. Deshalb nicht aus dem IP-Symcon-Modul heraus ausführen, sondern einmalig über SSH/Terminal. Passe den Modulpfad bei Bedarf an:

```bash
/root/roborock-test/bin/python \
  /PFAD/ZUR/MODULBIBLIOTHEK/RoborockQrevoEdgeT/scripts/roborock_bridge.py \
  --session-file /root/.cache/roborock-user-params.pkl \
  --cache-file /root/.cache/roborock-cache-data.pkl \
  --device-name "Roborock Qrevo EdgeT" \
  --export-dir /root/roborock_export \
  --login
```

Die Sitzung wird mit dem von `python-roborock` bereitgestellten Speichermechanismus gesichert und die Session-Datei soweit möglich mit Dateirechten `0600` versehen. Sie enthält sensible Anmeldedaten; nicht veröffentlichen oder in Support-Logs einfügen.

**Wichtig zum Linux-Benutzer:** Der Login muss unter demselben Benutzer erfolgen, unter dem IP-Symcon die Python-Brücke ausführt, oder die Pfade und Berechtigungen müssen entsprechend angepasst werden. Wenn IP-Symcon nicht als `root` läuft, sind `/root/...`-Pfade für den Dienst normalerweise nicht zugänglich. Lege in diesem Fall Python-Umgebung, Session- und Cache-Dateien in einen für das Dienstkonto zugänglichen Pfad und passe die Instanzeigenschaften entsprechend an.

## Instanz konfigurieren

Die Vorgabewerte orientieren sich an der bisherigen Einrichtung:

- Python: `/root/roborock-test/bin/python`
- Gerätename: `Roborock Qrevo EdgeT`
- Session: `/root/.cache/roborock-user-params.pkl`
- Cache: `/root/.cache/roborock-cache-data.pkl`
- Export: `/root/roborock_export`
- Pollintervall: 300 Sekunden
- Timeout: 120 Sekunden

Prüfe die Pfade in der Konfiguration und betätige „Verbindung testen und Werte aktualisieren“. Nach erfolgreicher Abfrage wird ein Medienobjekt **Roborock-Karte** unterhalb der Instanz aktualisiert.

## Steuerung

Die Instanzvariablen `Reinigung starten`, `Reinigung pausieren`, `Reinigung stoppen` und `Zur Ladestation fahren` sind Aktionsschalter. Das sind Befehlsanforderungen; das Gerät bestätigt den tatsächlichen Zustand erst bei einem späteren Statusabruf.

Raumreinigung wird als öffentliche Modulfunktion aufgerufen. Beispiel für ein IP-Symcon-PHP-Skript (Instanz-ID `12345`, Segment 1, einmal):

```php
RQT_CleanRoom(12345, 1, 1);
```

Für dein Gerät sind aus der aktuellen API folgende Zuordnungen bekannt:

- Segment 1: Esszimmer, IoT-ID `51085016`
- Segment 2: Wohnzimmer, IoT-ID `50947290`
- Segment 4: Schlafzimmer, IoT-ID `51001075`

Die Liste kann sich ändern; die Brücke prüft deshalb bei der Raumreinigung zusätzlich die aktuelle Geräte-/Kartenantwort. Segment 3 ist derzeit unzugeordnet und wird nicht zur Reinigung freigegeben. Ein positives Rückgabeergebnis bedeutet, dass die Befehlsanforderung an `python-roborock` übergeben wurde; es garantiert nicht, dass das Gerät die Reinigung bereits begonnen hat.

## Fehlerbehebung

- **„Python ist nicht ausführbar“**: `PythonPath` prüfen und sicherstellen, dass das Symcon-Dienstkonto die Datei ausführen darf.
- **„Keine Roborock-Anmeldung gefunden“**: Login-Befehl ausführen; sicherstellen, dass `SessionFile` im Modul und im Login-Befehl identisch ist.
- **Permission denied**: Rechte für Python-Umgebung, Session, Cache und Exportverzeichnis prüfen. Nicht einfach Zugangsdaten mit global lesbaren Dateirechten freigeben.
- **„proc_open ist deaktiviert“**: PHP-Konfiguration der IP-Symcon-Installation prüfen.
- **Karte fehlt**: Im Debug-Log die Fehlermeldung der Python-Brücke lesen und den Exportpfad kontrollieren.
- **Raum wird abgewiesen**: Erst manuell aktualisieren und `Räume (JSON)` kontrollieren. Unbekannte oder leere IoT-IDs sind absichtlich nicht steuerbar.

## Hintergrund / API

- `python-roborock`: https://github.com/Python-roborock/python-roborock
- IP-Symcon-Modulstruktur: https://www.symcon.de/de/service/dokumentation/entwicklerbereich/sdk-tools/sdk-php/struktur/
