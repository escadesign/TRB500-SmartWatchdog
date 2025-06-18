import configparser
import logging
import time
import paramiko
import ollama
import threading
import copy
from collections import deque
from flask import Flask, render_template

# --- Konfiguration und Globale Variablen ---
# Lese die Konfiguration aus der config.ini-Datei
config = configparser.ConfigParser()
config.read('config.ini')

# Extrahiere Konfigurationen
TRB500_IP = config.get('TRB500', 'ip')
TRB500_USER = config.get('TRB500', 'user')
TRB500_PASS = config.get('TRB500', 'password')
PROMPT = config.get('Ollama', 'prompt')
OLLAMA_MODEL = config.get('Ollama', 'model')
CHECK_INTERVAL = config.getint('General', 'check_interval')
WEB_SERVER_PORT = config.getint('WebServer', 'port', fallback=5000)
WEB_SERVER_HOST = config.get('WebServer', 'host', fallback='0.0.0.0')


# Thread-sichere Datenstruktur für den Austausch zwischen Watchdog und Webserver
shared_data = {
    "log_messages": deque(maxlen=100),  # Speichert die letzten 100 Log-Nachrichten
    "router_data": {},
    "ollama_activity": {"prompt": "N/A", "response": "No activity yet.", "timestamp": ""},
    "settings": {}
}
data_lock = threading.Lock()

# --- Logging Konfiguration ---
# Benutzerdefinierter Handler, der Logs in unsere shared_data-Struktur schreibt
class ListLogHandler(logging.Handler):
    def __init__(self, log_list):
        super().__init__()
        self.log_list = log_list

    def emit(self, record):
        log_entry = self.format(record)
        with data_lock:
            self.log_list.append(log_entry)

# Richte das Logging ein
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.StreamHandler()]) # Loggt weiterhin auf die Konsole

# Füge unseren custom Handler hinzu
list_handler = ListLogHandler(shared_data['log_messages'])
list_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(list_handler)


# --- Watchdog-Logik ---
def get_router_data(ssh):
    """Holt Daten vom Router und gibt sie als Dictionary zurück."""
    try:
        stdin, stdout, stderr = ssh.exec_command("gsmctl -q")
        signal_quality = stdout.read().decode().strip()

        stdin, stdout, stderr = ssh.exec_command("gsmctl -M")
        network_mode = stdout.read().decode().strip()
        
        stdin, stdout, stderr = ssh.exec_command("ifconfig wwan0 | grep 'RX packets'")
        network_stats = stdout.read().decode().strip()

        router_data = {
            "signal_quality": signal_quality,
            "network_mode": network_mode,
            "network_stats": network_stats,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        return router_data
    except Exception as e:
        logging.error(f"Fehler beim Abrufen der Router-Daten: {e}")
        return None

def analyze_with_ollama(data):
    """Analysiert die Daten mit Ollama."""
    try:
        full_prompt = f"{PROMPT}\n\nRouter Data:\n{data}"
        logging.info(f"Sende Anfrage an Ollama mit Modell {OLLAMA_MODEL}...")
        
        response = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[{'role': 'user', 'content': full_prompt}]
        )
        analysis_result = response['message']['content']
        logging.info(f"Antwort von Ollama erhalten: {analysis_result}")

        # Update der Ollama-Aktivität für das Webinterface
        with data_lock:
            shared_data["ollama_activity"] = {
                "prompt": full_prompt,
                "response": analysis_result,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            }
        return analysis_result
    except Exception as e:
        logging.error(f"Fehler bei der Kommunikation mit Ollama: {e}")
        return None

def watchdog_loop():
    """Die Hauptschleife des Watchdogs, die in einem separaten Thread läuft."""
    logging.info("Watchdog-Thread gestartet.")
    while True:
        try:
            logging.info("Stelle SSH-Verbindung zum TRB500 her...")
            with paramiko.SSHClient() as ssh:
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(TRB500_IP, username=TRB500_USER, password=TRB500_PASS, timeout=10)
                logging.info("SSH-Verbindung erfolgreich hergestellt.")

                router_data = get_router_data(ssh)
                if router_data:
                    logging.info(f"Router-Daten erfolgreich abgerufen: {router_data}")
                    with data_lock:
                        shared_data["router_data"] = router_data
                    
                    analyze_with_ollama(str(router_data))
                else:
                    logging.warning("Konnte keine Router-Daten abrufen.")

        except paramiko.AuthenticationException:
            logging.error("Authentifizierung fehlgeschlagen. Bitte überprüfe User/Passwort.")
        except Exception as e:
            logging.error(f"Ein Fehler im Watchdog-Loop ist aufgetreten: {e}")

        logging.info(f"Warte {CHECK_INTERVAL} Sekunden bis zur nächsten Prüfung.")
        time.sleep(CHECK_INTERVAL)


# --- Flask Web Server ---
# template_folder='.' weist Flask an, die HTML-Datei im selben Ordner zu suchen.
app = Flask(__name__, template_folder='.')

@app.route('/')
def index():
    """Rendert die Hauptseite mit den aktuellen Daten."""
    with data_lock:
        data_copy = copy.deepcopy(shared_data)
        data_copy["log_messages"] = list(data_copy["log_messages"])
    return render_template('index.html', data=data_copy)


# --- Hauptausführung ---
if __name__ == '__main__':
    # Bereite die anzuzeigenden Einstellungen vor (Passwörter entfernen!)
    settings_to_display = {
        'TRB500': {'ip': TRB500_IP, 'user': TRB500_USER},
        'Ollama': {'model': OLLAMA_MODEL, 'prompt': PROMPT},
        'General': {'check_interval': CHECK_INTERVAL},
        'WebServer': {'host': WEB_SERVER_HOST, 'port': WEB_SERVER_PORT}
    }
    with data_lock:
        shared_data['settings'] = settings_to_display

    # Starte den Watchdog in einem separaten Thread
    watchdog_thread = threading.Thread(target=watchdog_loop, daemon=True)
    watchdog_thread.start()

    # Starte den Flask Webserver im Hauptthread
    logging.info(f"Webserver wird gestartet auf http://{WEB_SERVER_HOST}:{WEB_SERVER_PORT}")
    app.run(host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)
