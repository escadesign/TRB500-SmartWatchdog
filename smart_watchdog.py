import configparser
import logging
import time
import paramiko
import ollama
import threading
import copy
import re
from collections import deque
from flask import Flask, render_template

# --- Konfiguration ---
config = configparser.ConfigParser()
config.read('config.ini')

ROUTER_HOST = config.get('ROUTER', 'host')
ROUTER_USER = config.get('ROUTER', 'user')
ROUTER_PASS = config.get('ROUTER', 'password')

LLM_API_URL = config.get('LLM', 'api_url')
LLM_MODEL = config.get('LLM', 'model')

WATCHDOG_CHECK_INTERVAL = config.getint('WATCHDOG', 'check_interval')
WATCHDOG_LOG_FILE = config.get('WATCHDOG', 'log_file')

WEB_SERVER_HOST = config.get('WebServer', 'host', fallback='0.0.0.0')
WEB_SERVER_PORT = config.getint('WebServer', 'port', fallback=5000)

# --- LLM-Prompt laden ---
with open('llm_prompt_template.txt', 'r', encoding='utf-8') as f:
    PROMPT_TEMPLATE = f.read()

# --- Thread-sichere Datenstruktur ---
shared_data = {
    "log_messages": deque(maxlen=100),
    "router_data": {},
    "ollama_activity": {"prompt": "N/A", "response": "No activity yet.", "timestamp": ""},
    "settings": {}
}
data_lock = threading.Lock()

# --- Logging ---
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

# --- Watchdog-Logik ---
def get_router_status(ssh):
    try:
        # Signalqualität
        stdin, stdout, stderr = ssh.exec_command("gsmctl -q")
        signal_quality = stdout.read().decode().strip()

        # Modus
        stdin, stdout, stderr = ssh.exec_command("gsmctl -M")
        network_mode = stdout.read().decode().strip()

        # CA-Band Parsing (robust, Debuglog der Rohdaten)
        stdin, stdout, stderr = ssh.exec_command("gsmctl -A 'AT+QCAINFO'")
        ca_info = stdout.read().decode().strip()
        logging.info(f"QCAINFO Rohdaten:\n{ca_info}")  # DEBUGLOG

        active_nr_band = None
        active_lte_bands = set()
        found_any = False
        for line in ca_info.splitlines():
            if line.strip():
                found_any = True
            # NR5G: Suche nach 'NR5G BAND nXX' oder 'NR5G BAND XX'
            match_nr = re.search(r'NR5G BAND n?(\d+)', line)
            if match_nr:
                active_nr_band = match_nr.group(1)
            # LTE: Suche nach 'LTE BAND XX'
            match_lte = re.search(r'LTE BAND (\d+)', line)
            if match_lte:
                active_lte_bands.add(match_lte.group(1))

        router_data = {
            "signal_quality": signal_quality or 'Keine Daten',
            "network_mode": network_mode or 'Keine Daten',
            "active_nr_band": (
                active_nr_band if active_nr_band else
                ("keine Bandinfo" if found_any else "N/A")
            ),
            "active_lte_bands": (
                ":".join(sorted(active_lte_bands)) if active_lte_bands else
                ("keine Bandinfo" if found_any else "N/A")
            ),
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        return router_data
    except Exception as e:
        logging.error(f"Fehler beim Abrufen der Router-Daten: {e}")
        return None

def analyze_with_ollama(router_data):
    try:
        prompt = PROMPT_TEMPLATE.format(
            data=router_data,
            active_nr_band=router_data.get("active_nr_band", "N/A"),
            active_lte_bands=router_data.get("active_lte_bands", "N/A")
        )
        logging.info(f"Sende Anfrage an Ollama mit Modell {LLM_MODEL}...")
        response = ollama.chat(
            model=LLM_MODEL,
            messages=[{'role': 'user', 'content': prompt}]
        )
        analysis_result = response['message']['content']
        logging.info(f"Antwort von Ollama erhalten: {analysis_result}")

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

def execute_router_action(ssh, action):
    try:
        if action.startswith("WAIT"):
            logging.info(f"LLM empfiehlt: {action.replace('WAIT:', '').strip()} Minuten warten.")
            return
        if action == "RESTART_MODEM":
            logging.info("LLM empfiehlt: Modem neu starten...")
            ssh.exec_command("gsmctl -r")
            return
        if action == "RESET_BANDS":
            logging.info("LLM empfiehlt: Band-Locks zurücksetzen und Modem neu starten.")
            ssh.exec_command("gsmctl -A 'AT+QNWPREFCFG=\"lte_band\",1:3:20'")
            ssh.exec_command("gsmctl -A 'AT+QNWPREFCFG=\"nr5g_band\",3'")
            ssh.exec_command("gsmctl -r")
            return
        if action.startswith("SET_LTE_BANDS:"):
            bands = action.replace("SET_LTE_BANDS:", "").strip()
            logging.info(f"LLM empfiehlt: Band-Lock für LTE-Bänder {bands} setzen und Modem neu starten.")
            ssh.exec_command(f"gsmctl -A 'AT+QNWPREFCFG=\"lte_band\",{bands}'")
            ssh.exec_command("gsmctl -r")
            return
        if action.startswith("SET_NR5G_BANDS:"):
            bands = action.replace("SET_NR5G_BANDS:", "").strip()
            logging.info(f"LLM empfiehlt: Band-Lock für NR5G-Bänder {bands} setzen und Modem neu starten.")
            ssh.exec_command(f"gsmctl -A 'AT+QNWPREFCFG=\"nr5g_band\",{bands}'")
            ssh.exec_command("gsmctl -r")
            return
        if action == "FULL_SCAN":
            logging.info("LLM empfiehlt: Vollständigen Scan (FULL_SCAN) – Achtung: Verbindung wird getrennt.")
            ssh.exec_command("gsmctl -A 'AT+QSCAN=3,1'")
            return
        logging.warning(f"Unbekannte Aktion von LLM: {action}")
    except Exception as e:
        logging.error(f"Fehler bei der Ausführung der Router-Aktion '{action}': {e}")

def watchdog_loop():
    logging.info("Watchdog-Thread gestartet.")
    while True:
        try:
            logging.info("Stelle SSH-Verbindung zum TRB500 her...")
            with paramiko.SSHClient() as ssh:
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                ssh.connect(ROUTER_HOST, username=ROUTER_USER, password=ROUTER_PASS, timeout=10)
                logging.info("SSH-Verbindung erfolgreich hergestellt.")

                router_data = get_router_status(ssh)
                if router_data:
                    logging.info(f"Router-Daten erfolgreich abgerufen: {router_data}")
                    with data_lock:
                        shared_data["router_data"] = router_data
                    action = analyze_with_ollama(router_data)
                    if action:
                        execute_router_action(ssh, action.strip())
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
        'LLM': {'model': LLM_MODEL},
        'WebServer': {'host': WEB_SERVER_HOST, 'port': WEB_SERVER_PORT}
    }
    with data_lock:
        shared_data['settings'] = settings_to_display

    watchdog_thread = threading.Thread(target=watchdog_loop, daemon=True)
    watchdog_thread.start()

    logging.info(f"Webserver wird gestartet auf http://{WEB_SERVER_HOST}:{WEB_SERVER_PORT}")
    app.run(host=WEB_SERVER_HOST, port=WEB_SERVER_PORT)
