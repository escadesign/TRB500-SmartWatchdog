# Smart Watchdog für Teltonika TRB500

Dieses Projekt realisiert einen intelligenten Watchdog auf einem Raspberry Pi 4. Das Skript überwacht kontinuierlich die Mobilfunkparameter eines Teltonika TRB500 Routers, lässt ein lokales LLM (via Ollama) eine Handlungsentscheidung treffen und führt diese automatisiert aus.

**Philosophie:** Zero-Pain-Setup, robust, minimalistisch, erweiterbar.

---

### Schritt 1: System-Voraussetzungen (Raspberry Pi 4)

1.  **Raspberry Pi OS Lite (64-bit):** Stellen Sie sicher, dass Ihr System auf dem neuesten Stand ist.
    ```bash
    sudo apt update && sudo apt upgrade -y
    ```

2.  **Python 3 & Pip:** Sollte bereits installiert sein. Überprüfen Sie dies mit `python3 --version`.

3.  **Ollama installieren:** Ollama dient zur Ausführung des lokalen LLMs. Die Installation ist ein Einzeiler. [15, 18]
    ```bash
    curl -fsSL https://ollama.com/install.sh | sh
    ```

4.  **Lokales LLM herunterladen:** Wir verwenden `phi-3` (ein gutes, kompaktes Modell) oder `tinyllama` für maximale Performance. `phi-3` ist etwas fähiger.
    ```bash
    # Empfohlen:
    ollama pull phi-3

    # Alternative für schwächere Systeme:
    ollama pull tinyllama
    ```
    Das Modell wird heruntergeladen und steht danach für Anfragen bereit. [4, 10]

---

### Schritt 2: Projekt-Setup

1.  **Klone oder erstelle das Projektverzeichnis:**
    ```bash
    mkdir ~/teltonika_watchdog
    cd ~/teltonika_watchdog
    ```
    Erstellen Sie die Dateien `smart_watchdog.py`, `config.ini`, `llm_prompt_template.txt`, und `requirements.txt` in diesem Verzeichnis und fügen Sie die untenstehenden Inhalte ein.

2.  **Python-Abhängigkeiten installieren:**
    ```bash
    pip install -r requirements.txt
    pip install flask
    ```

3.  **Konfiguration anpassen:**
    Öffnen Sie `config.ini` und tragen Sie die IP-Adresse sowie die Anmeldedaten für Ihren Teltonika-Router ein. Passen Sie bei Bedarf den Namen des LLM-Modells an.

---

### Schritt 3: Watchdog ausführen

1.  **Manueller Start:**
    Führen Sie das Skript direkt aus, um die Funktionalität zu testen.
    ```bash
    python3 smart_watchdog.py
    ```
    Das Skript beginnt sofort mit der Arbeit und gibt Statusmeldungen sowie Aktionen im Logfile (`watchdog.log`) und auf der Konsole aus.

2.  **Als Systemd-Service einrichten (für 24/7-Betrieb):**
    Damit das Skript automatisch nach einem Neustart startet und im Hintergrund läuft, erstellen wir einen Service.

    *   **Service-Datei erstellen:**
        ```bash
        sudo nano /etc/systemd/system/smart_watchdog.service
        ```

    *   **Inhalt einfügen** (ersetzen Sie `<USER>` durch Ihren Benutzernamen, z.B. `pi`):
        ```ini
        [Unit]
        Description=Smart Watchdog for Teltonika Router
        After=network.target ollama.service

        [Service]
        User=<USER>
        WorkingDirectory=/home/<USER>/teltonika_watchdog
        ExecStart=/usr/bin/python3 smart_watchdog.py
        Restart=always
        RestartSec=10

        [Install]
        WantedBy=multi-user.target
        ```

    *   **Service aktivieren und starten:**
        ```bash
        sudo systemctl daemon-reload
        sudo systemctl enable smart_watchdog.service
        sudo systemctl start smart_watchdog.service
        ```

    *   **Status überprüfen:**
        ```bash
        sudo systemctl status smart_watchdog.service
        tail -f /home/<USER>/teltonika_watchdog/watchdog.log
        ```

---

### Schritt 4: Hardware-Watchdog des Raspberry Pi aktivieren (Optional aber empfohlen)

Der Hardware-Watchdog startet den Raspberry Pi neu, falls das gesamte Betriebssystem einfriert. [16, 17, 19]

1.  **Konfiguration bearbeiten:**
    ```bash
    sudo nano /boot/config.txt
    ```
    Fügen Sie diese Zeile am Ende hinzu:
    ```
    dtparam=watchdog=on
    ```

2.  **Systemd konfigurieren:**
    ```bash
    sudo nano /etc/systemd/system.conf
    ```
    Entkommentieren und setzen Sie die folgende Zeile. Dies weist `systemd` an, den Hardware-Watchdog regelmäßig zu "füttern". Wenn das System hängt, stoppt das Füttern und der Pi startet nach 15 Sekunden neu.
    ```
    RuntimeWatchdogSec=15s
    ```

3.  **Neustart:**
    ```bash
    sudo reboot
    ```
    Das System ist jetzt doppelt abgesichert: Der `systemd`-Service startet das Skript bei einem Absturz neu, und der Hardware-Watchdog startet den gesamten Pi bei einem System-Freeze neu.

## Projektstruktur

```
SmartWatchdog/
├── config.ini
├── llm_prompt_template.txt
└── watchdog.py
└── README.md
```

- `config.ini`: Konfigurationsdatei für Router-Verbindung, LLM-Einstellungen, Logging und Watchdog-Parameter.
- `llm_prompt_template.txt`: Vorlage für den Prompt, der an das lokale LLM gesendet wird.
- `watchdog.py`: Das Hauptskript, das die Logik für Datensammlung, LLM-Interaktion und Aktionsausführung enthält.
- `README.md`: Diese Datei mit Installations- und Nutzungshinweisen.

## Systemanforderungen

- **Raspberry Pi 4** (mit Raspberry Pi OS Lite empfohlen)
- **Python 3** (vorinstalliert auf den meisten Raspberry Pi OS Installationen)
- **Teltonika TRB500** 5G/LTE-Router mit SSH-Zugang und aktiver Firmware (TRB5_R_00.07.05 oder neuer empfohlen).
- **Lokales LLM** (z.B. Ollama, llama.cpp REST-API) läuft auf dem Raspberry Pi oder einem erreichbaren Gerät im Netzwerk, mit einer REST-API, die Text-Prompts akzeptiert und Klartext-Antworten liefert.

## Einrichtung

### 1. Raspberry Pi Vorbereitung

1.  **SSH aktivieren**: Stellen Sie sicher, dass SSH auf Ihrem Raspberry Pi aktiviert ist.
2.  **System aktualisieren**:
    ```bash
    sudo apt update && sudo apt upgrade -y
    ```
3.  **Git installieren** (falls nicht vorhanden):
    ```bash
    sudo apt install git -y
    ```
4.  **Projekt klonen**: Klonen Sie dieses Repository auf Ihren Raspberry Pi, z.B. nach `/opt/smart_watchdog`.
    ```bash
    sudo mkdir -p /opt/smart_watchdog
    sudo chown pi:pi /opt/smart_watchdog # Oder den Benutzer, unter dem das Skript laufen soll
    cd /opt/smart_watchdog
    git clone <URL_ZU_DIESEM_REPO> .
    ```
    *Hinweis: Ersetzen Sie `<URL_ZU_DIESEM_REPO>` durch die tatsächliche URL dieses GitHub/GitLab-Repositorys.*

5.  **Python-Abhängigkeiten installieren**:
    ```bash
    pip install paramiko requests
    ```

### 2. Teltonika TRB500 Router Konfiguration

1.  **SSH-Zugang aktivieren**: Stellen Sie sicher, dass SSH auf Ihrem TRB500 Router aktiviert ist (WebUI: `System` -> `Administration` -> `Access Control` -> `SSH`).
2.  **Band-Auswahl auf Manuell setzen**: Um zu verhindern, dass die WebUI Ihre automatisierten Band-Locks überschreibt, setzen Sie die Band-Auswahl in der WebUI auf `Manual` (WebUI: `Network` -> `Mobile` -> `General` -> `Band selection`).
3.  **SSH-Schlüssel (Empfohlen)**: Für eine sicherere und passwortlose Authentifizierung mit dem Router, generieren Sie ein SSH-Schlüsselpaar auf dem Raspberry Pi und kopieren Sie den öffentlichen Schlüssel auf den Router.
    ```bash
    # Auf dem Raspberry Pi:
    ssh-keygen -t rsa -b 4096
    ssh-copy-id root@<ROUTER_IP> # Ersetzen Sie <ROUTER_IP> mit der IP-Adresse Ihres Routers
    ```
    Passen Sie dann `config.ini` an, um den Pfad zu Ihrem privaten Schlüssel anzugeben.

### 3. Konfiguration des Smart Watchdog Skripts (`config.ini`)

Öffnen Sie die Datei `config.ini` im Projektverzeichnis und passen Sie die Parameter an Ihre Umgebung an:

```ini
[ROUTER]
IP = 192.168.1.1       ; IP-Adresse Ihres Teltonika TRB500 Routers
USERNAME = root          ; SSH-Benutzername für den Router (Standard: root)
# PRIVATE_KEY_PATH = /home/pi/.ssh/id_rsa ; Pfad zum SSH-Privatschlüssel (auskommentieren, wenn Passwort verwendet wird)
# PASSWORD = your_ssh_password   ; SSH-Passwort (nur verwenden, wenn kein privater Schlüssel)

[LLM]
API_ENDPOINT = http://localhost:8000/generate ; URL der REST-API Ihres lokalen LLM (z.B. Ollama)
# LLM_MODEL = TinyLlama # Optional: Der Name des Modells, das Sie verwenden (nicht direkt im Skript genutzt, nur zur Info)
# LLM_TIMEOUT = 30 # Optional: Timeout für LLM-Anfragen in Sekunden

[WATCHDOG]
POLLING_INTERVAL_SECONDS = 60    ; Wartezeit zwischen den Zyklen (30-60 Sekunden empfohlen)
MAX_ACTIONS_PER_HOUR = 3         ; Maximale Anzahl von Aktionen pro Stunde, um Loops zu verhindern
LOG_FILE = /var/log/smart_watchdog.log ; Pfad zur Logdatei des Watchdogs
MANUAL_OVERRIDE_FILE = /tmp/smart_watchdog_manual_override ; Pfad für die manuelle Übersteuerungsdatei
```

### 4. LLM Prompt Template (`llm_prompt_template.txt`)

Die Datei `llm_prompt_template.txt` enthält die Vorlage des Prompts, der an Ihr LLM gesendet wird. Sie können diese anpassen, um die Instruktionen an Ihr spezifisches LLM zu optimieren. Der Platzhalter `{{CELLULAR_DATA}}` wird automatisch mit den gesammelten Mobilfunkdaten gefüllt.

## Ausführung des Watchdogs

### Manuelle Ausführung (zu Testzwecken)

Navigieren Sie in das Projektverzeichnis und führen Sie das Skript aus:

```bash
cd /opt/smart_watchdog
python3 watchdog.py
```

Um das Skript zu stoppen, drücken Sie `Ctrl+C`.

### Dauerhafter Betrieb mit `systemd` (Empfohlen)

Für einen robusten 24/7-Betrieb und als "Watchdog für den Watchdog" wird die Verwendung eines `systemd`-Dienstes empfohlen.

1.  **Systemd-Dienstdatei erstellen**:
    ```bash
    sudo nano /etc/systemd/system/smart_watchdog.service
    ```
    Fügen Sie den folgenden Inhalt ein (passen Sie `User` und `Group` bei Bedarf an):

    ```ini
    [Unit]
    Description=Smart Watchdog for Teltonika TRB500
    After=network.target

    [Service]
    Type=simple
    User=pi
    Group=pi
    WorkingDirectory=/opt/smart_watchdog
    ExecStart=/usr/bin/python3 /opt/smart_watchdog/watchdog.py
    Restart=on-failure
    RestartSec=10
    StandardOutput=journal
    StandardError=journal
    SyslogIdentifier=smart_watchdog

    [Install]
    WantedBy=multi-user.target
    ```

2.  **Systemd reloaded und Dienst starten**:
    ```bash
    sudo systemctl daemon-reload
    sudo systemctl enable smart_watchdog.service # Autostart beim Booten aktivieren
    sudo systemctl start smart_watchdog.service
    ```

3.  **Status überprüfen**:
    ```bash
    sudo systemctl status smart_watchdog.service
    journalctl -u smart_watchdog.service -f
    ```

## Manuelle Übersteuerung

Sie können das Verhalten des Watchdogs temporär deaktivieren, indem Sie eine leere Datei am in `config.ini` angegebenen Pfad (`MANUAL_OVERRIDE_FILE`) erstellen. Standardmäßig ist dies `/tmp/smart_watchdog_manual_override`.

```bash
# Watchdog pausieren (er liest weiterhin Daten, führt aber keine LLM-Interaktion/Aktionen aus)
touch /tmp/smart_watchdog_manual_override

# Watchdog wieder aktivieren
rm /tmp/smart_watchdog_manual_override
```

Dies ist nützlich für Wartungsarbeiten oder manuelle Tests am Router.

## Logging

Alle Aktionen und Kontextdaten werden in der in `config.ini` definierten Logdatei (`LOG_FILE`) gespeichert. Standardmäßig ist dies `/var/log/smart_watchdog.log`.

## Erweiterbarkeit

Die Architektur ist darauf ausgelegt, leicht erweiterbar zu sein:

-   **Neue Auswerteparameter**: Fügen Sie einfach weitere `gsmctl -A 'AT+...'`-Befehle in `get_cellular_data()` hinzu und passen Sie die Parsing-Funktionen an.
-   **Zusätzliche Aktionsoptionen**: Erweitern Sie die Funktion `_perform_router_action()` um neue Befehle und deren Ausführung. Stellen Sie sicher, dass Ihr LLM diese neuen Kommandos auch generieren kann (ggf. passen Sie `llm_prompt_template.txt` an).
-   **Alternative LLM-APIs**: Die Funktion `send_to_llm()` kann angepasst werden, um mit verschiedenen LLM-REST-APIs zu kommunizieren (z.B. OpenAI-kompatible Endpunkte, HuggingFace Inference API).

## Fehlerbehandlung

-   **LLM-Fallback**: Wenn das LLM nicht antwortet oder eine unbrauchbare Antwort liefert, führt das Skript standardmäßig die Aktion "Keine Aktion nötig" aus.
-   **Aktions-Rate-Limit**: Es gibt eine konfigurierbare Begrenzung der Aktionen pro Stunde, um Endlosschleifen zu verhindern.
-   **SSH-Verbindungsversuche**: Das Skript versucht, die SSH-Verbindung automatisch wiederherzustellen, falls sie unterbrochen wird.

---

Viel Erfolg beim Optimieren Ihrer Mobilfunkverbindung! 
