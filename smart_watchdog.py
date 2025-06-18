import configparser
import logging
import time
import paramiko
import ollama
import threading
import copy
from collections import deque
from flask import Flask, render_template
import os

# --- Band-Blacklist (LTE B28, 5G n28, n78) ---
BLACKLISTED_BANDS_LTE = [28]
BLACKLISTED_BANDS_NR = [28, 78]

# --- Konfiguration und Globale Variablen ---
config = configparser.ConfigParser()
config.read('config.ini')

ROUTER_HOST = config.get('ROUTER', 'host')
ROUTER_USER = config.get('ROUTER', 'user')
ROUTER_PASS = config.get('ROUTER', 'password')

LLM_API_URL = config.get('LLM', 'api_url')
LLM_MODEL = config.get('LLM', 'model')

WATCHDOG_CHECK_INTERVAL = config.getint('WATCHDOG', 'check_interval')
WATCHDOG_MAX_ACTIONS_PER_HOUR = config.getint('WATCHDOG', 'max_actions_per_hour')
WATCHDOG_LOG_FILE = config.get('WATCHDOG', 'log_file')
WATCHDOG_MANUAL_OVERRIDE_FLAG = config.get('WATCHDOG', 'manual_override_flag')

WEB_SERVER_HOST = config.get('WebServer', 'host', fallback='0.0.0.0')
WEB_SERVER_PORT = config.getint('WebServer', 'port', fallback=5000)

# --- Lese Prompt-Template ein ---
def read_prompt_template():
    with open('llm_prompt_template.txt', encoding='utf-8') as f:
        return f.read()

PROMPT_TEMPLATE = read_prompt_template()

# Thread-sichere Datenstruktur für den Austausch zwischen Watchdog und Webserver
shared_data = {
    "log_messages": deque(maxlen=100),  # Speichert die letzten 100 Log-Nachrichten
    "router_data": {},
    "bands_data": [],
    "ollama_activity": {"prompt": "N/A", "response": "No activity yet.", "timestamp": ""},
    "settings": {}
}
data_lock = threading.Lock()

# --- Logging Konfiguration ---
class ListLogHandler(logging.Handler):
    def __init__(self, log_list):
        super().__init__()
        self.log_list = log_list

    def emit(self, record):
        log_entry = self.format(record)
        with data_lock:
            self.log_list.append(log_entry)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s',
                    handlers=[logging.StreamHandler()])
list_handler = ListLogHandler(shared_data['log_messages'])
list_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
logging.getLogger().addHandler(list_handler)

# --- Band-Parsing Hilfsfunktionen ---
def band_friendly_name(entry):
    rat = entry.get('rat', '')
    band = entry.get('band', '?')
    if rat == 'NR5G':
        return f"n{band}"
    return f"B{band}"

def band_is_blacklisted(entry):
    rat = entry.get('rat', '')
    band = int(entry.get('band', -1))
    if rat == 'NR5G':
        return band in BLACKLISTED_BANDS_NR
    elif rat == 'LTE':
        return band in BLACKLISTED_BANDS_LTE
    return False

def parse_scan_results(raw_lines):
    """
    raw_lines: Liste von '+QSCAN: ...'-Textzeilen vom Router
    Gibt Liste von Dicts: [{'rat':'LTE', 'band':3, 'RSRP':-80, 'RSRQ':-10, 'SINR':12}, ...]
    """
    results = []
    for line in raw_lines:
        try:
            if "+QSCAN:" in line:
                parts = line.split(",")
                rat = parts[0].split('"')[1]  # "LTE" oder "NR5G"
                band = int(parts[-1])
                rsrp = int(parts[-5])
                rsrq = int(parts[-4])
                sinr = int(parts[-3])
                results.append({
                    'rat': rat,
                    'band': band,
                    'RSRP': rsrp,
                    'RSRQ': rsrq,
                    'SINR': sinr,
                })
        except Exception:
            continue
    return results

def build_bands_prompt_block(scan_results):
    lte_bands = []
    nr_bands = []
    for entry in scan_results:
        name = band_friendly_name(entry)
        if band_is_blacklisted(entry):
            continue  # Band nicht anzeigen!
        values = f"RSRP {entry['RSRP']}, RSRQ {entry['RSRQ']}, SINR {entry['SINR']}"
        if entry['rat'] == 'NR5G':
            nr_bands.append(f"{name}: {values}")
        else:
            lte_bands.append(f"{name}: {values}")
    block = "## Aktuelle LTE-Bänder und Werte:\n" + ("\n".join(lte_bands) if lte_bands else "Keine LTE-Bänder erkannt.")
    block += "\n## Aktuelle 5G-Bänder und Werte:\n" + ("\n".join(nr_bands) if nr_bands else "Keine 5G-Bänder erkannt.")
    return block

def get_band_context(scan_results):
    # Zeige für den Prompt die aktuell genutzten Bänder (außer Blacklist)
    active_lte = [f"B{e['band']}" for e in scan_results if e['rat'] == 'LTE' and not band_is_blacklisted(e)]
    active_nr = [f"n{e['band']}" for e in scan_results if e['rat'] == 'NR5G' and not band_is_blacklisted(e)]
    active_lte_str = ", ".join(active_lte) if active_lte else "Keine"
    active_nr_str = ", ".join(active_nr) if active_nr else "Keine"
    return active_nr_str, active_lte_str

# --- SSH-Kommandos ---
def fetch_router_scan(ssh):
    # Holt die Bänder per AT-Kommando vom Router (QSCAN), gibt List of Lines zurück
    try:
        stdin, stdout, stderr = ssh.exec_command("gsmctl -A 'AT+QSCAN=3,1'")
        raw = stdout.read().decode().splitlines()
        return [line.strip() for line in raw if "+QSCAN:" in line]
    except Exception as e:
        logging.error(f"Fehler beim Band-Scan: {e}")
        return []

def get_router_data(ssh):
    """Holt Daten vom Router und gibt sie als Dictionary zurück."""
    try:
        stdin, stdout, stderr = ssh.exec_command("gsmctl -q")
        signal_quality = stdout.read().decode().strip()

        stdin, stdout, stderr = ssh.exec_command("gsmctl -M")
        network_mode = stdout.read().decode().strip()
        
        stdin, stdout, stderr = ssh.exec_command("ifconfig wwan0 | grep 'RX packets'")
        network_stats = stdout.read().decode().strip()

        # Bands per AT-Scan
        scan_lines = fetch_router_scan(ssh)
        bands_data = parse_scan_results(scan_lines)
        active_nr_band, active_lte_bands = get_band_context(bands_data)

        router_data = {
            "signal_quality": signal_quality if signal_quality else "Keine Daten",
            "network_mode": network_mode if network_mode else "Keine Daten",
            "network_stats": network_stats if network_stats else "Keine Daten",
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "active_nr_band": active_nr_band,
            "active_lte_bands": active_lte_bands
        }
        return router_data, bands_data
    except Exception as e:
        logging.error(f"Fehler beim Abrufen der Router-Daten: {e}")
        return None, []

# --- LLM Analyse ---
def analyze_with_ollama(router_data, bands_data):
    """Analysiert die Daten mit Ollama."""
    try:
        bands_prompt_block = build_bands_prompt_block(bands_data)
        prompt = PROMPT_TEMPLATE.format(
            data=(
                f"Signalqualität:\n{router_data['signal_quality']}\n"
                f"Netzwerk Modus:\n{router_data['network_mode']}\n"
                f"Netzwerk Stats:\n{router_data['network_stats']}\n"
                f"Zeitstempel: {router_data['timestamp']}\n"
                f"{bands_prompt_block}\n"
                f"active_nr_band: {router_data['active_nr_band']}\n"
                f"active_lte_bands: {router_data['active_lte_bands']}"
            ),
            active_nr_band=router_data['active_nr_band'],
            active_lte_bands=router_data['active_lte_bands']
        )

        logging.info(f"Sende Anfrage an Ollama mit Modell {LLM_MODEL}...")
        response = ollama.chat(
            model=LLM_MODEL,
            messages=[{'role': 'user', 'content': prompt}]
        )
        analysis_result = response['message']['content']
        logging.info(f"Antwort von Ollama erhalten: {analysis_result}")

        # Update der Ollama-Aktivität für das Webinterface
        with data_lock:
            shared_data["ollama_activity"] = {
                "prompt": prompt,
                "response": analysis_result,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            }
        return analysis_result
    except Exception as e:
        logging.error(f"Fehler bei der Kommunikation mit Ollama: {e}")
        return None

# --- Watchdog-Logik ---
def watchdog_loop():
    logging.info("Watchdog-Thread gestartet.")
    while True:
        try:
            logging.info("Stelle SSH-Verbindung zum TRB500 her...")
            with paramiko.SSHClient() as ssh:
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ROUTER_HOST, username=ROUTER_USER, password=ROUTER_PASS, timeout=10)
                logging.info("SSH-Verbindung erfolgreich hergestellt.")

                router_data, bands_data = get_router_data(ssh)
                if router_data:
                    logging.info(f"Router-Daten erfolgreich abgerufen: {router_data}")
                    with data_lock:
                        shared_data["router_data"] = router_data
                        shared_data["bands_data"] = bands_data
                    
                    analyze_with_ollama(router_data, bands_data)
                else:
                    logging.warning("Konnte keine Router-Daten abrufen.")

        except paramiko.AuthenticationException:
            logging.error("Authentifizierung fehlgeschlagen. Bitte überprüfe User/Passwort.")
        except Exception as e:
            logging.error(f"Ein Fehler im Watchdog-Loop ist aufgetreten: {e}")

        logging.info(f"Warte {WATCHDOG_CHECK_INTERVAL} Sekunden bis zur nächsten Prüfung.")
        time.sleep(WATCHDOG_CHECK_INTERVAL)

# --- Flask Web Server ---
app = Flask(__name__, template_folder='.')

@app.route('/')
def index():
    with data_lock:
        data_copy = copy.deepcopy(shared_data)
        data_copy["log_messages"] = list(data_copy["log_messages"])
    return render_template('index.html', data=data_copy)

# --- Hauptausführung ---
if __name__ == '__main__':
    settings_to_display = {
        'TRB500': {'ip': ROUTER_HOST, 'user': ROUTER_USER},
        'LLM': {'model': LLM_MODEL, 'api_url': LLM_API_URL},
        'General': {'check_interval': WATCHDOG_CHECK_INTERVAL},
        'WebServer': {'host': WEB_SERVER_HOST, 'port': WEB_SERVER_PORT}
    }
    with data_lock:
        shared_data['settings'] = settings_to_display

    watchdog_thread = threading.Thread(target=watchdog_loop, daemon=True)
    watchdog_thread.start()

    logging.info(f"Webserver wird gestartet auf http://{WEB_SERVER_HOST}:{WEB_SERVER_PORT}")
    app.run(host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)
