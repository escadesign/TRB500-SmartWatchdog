import configparser
import json
import logging
import os
import time
from collections import deque
from datetime import datetime, timedelta

import paramiko
import requests


class SSHManager:
    """Ein Kontextmanager für einfache SSH-Verbindungen mit Paramiko."""
    def __init__(self, host, user, password):
        self.host = host
        self.user = user
        self.password = password
        self.client = None

    def __enter__(self):
        try:
            self.client = paramiko.SSHClient()
            self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            self.client.connect(self.host, username=self.user, password=self.password, timeout=10)
            return self #.client
        except Exception as e:
            logging.error(f"SSH-Verbindung zu {self.host} fehlgeschlagen: {e}")
            # Um einen sauberen __exit__ zu gewährleisten, wird hier keine Exception geworfen
            # Stattdessen wird self.client None bleiben, was in execute_command geprüft wird.
            return self


    def execute_command(self, command):
        if not self.client:
            return None, "SSH-Client nicht verbunden"
        try:
            stdin, stdout, stderr = self.client.exec_command(command)
            output = stdout.read().decode('utf-8').strip()
            error = stderr.read().decode('utf-8').strip()
            if not output and error:
                 # Manchmal landen harmlose Statusmeldungen in stderr, nur echte Fehler loggen
                if "ERROR" in error:
                    logging.warning(f"Befehl '{command}' erzeugte einen Fehler: {error}")
            return output, error
        except Exception as e:
            logging.error(f"Fehler bei der Ausführung des Befehls '{command}': {e}")
            return None, str(e)


    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.client:
            self.client.close()

def get_modem_data(ssh_manager: SSHManager):
    """Liest relevante Mobilfunkparameter vom Router aus."""
    logging.info("Lese Mobilfunkdaten vom Router...")
    # AT+QCAINFO ist gut für aggregierte Bänder, AT+QENG für die primäre Zelle.
    serving_cell_cmd = "gsmctl -A 'AT+QENG=\"servingcell\"'"
    
    # Beispielhafte Abfrage, da -A Shell-Metazeichen evtl. nicht mag.
    output, _ = ssh_manager.execute_command(serving_cell_cmd)

    if not output or '\"servingcell\",' not in output:
        logging.warning("Konnte 'servingcell' nicht auslesen. Versuche Fallback.")
        return {"error": "Keine validen Daten von AT+QENG erhalten"}

    # Pragmatisches Parsen des Outputs. Dies muss ggf. an den exakten Output angepasst werden.
    # +QENG: "servingcell","NOCONN","LTE","FDD",262,01,9F8B018,300,1275,3,5,5,-96,-12,11,18,-
    try:
        parts = output.split(',')
        if "\"LTE\"" in parts[2]:
            return {
                "mode": "LTE",
                "band": f"B{parts[9]}",
                "rsrp": int(parts[12]),
                "rsrq": int(parts[13]),
                "sinr": int(parts[14]),
            }
        elif "\"NR5G\"" in parts[2]:
             return {
                "mode": "5G-NR",
                "band": f"N{parts[10]}",
                "rsrp": int(parts[13]),
                "rsrq": int(parts[14]),
                "sinr": int(parts[15]),
            }
    except (IndexError, ValueError) as e:
        logging.error(f"Fehler beim Parsen des 'servingcell' Outputs: {e}\nOutput war: {output}")
        return {"error": "Parsing fehlgeschlagen"}
    
    return {"error": "Unbekannter oder nicht verbundener Modus"}


def query_llm(context_data: dict, config: configparser.ConfigParser):
    """Sendet die Daten an das lokale LLM und holt eine Entscheidung ein."""
    if not context_data or "error" in context_data:
        logging.warning("Keine validen Kontextdaten für LLM-Anfrage.")
        return None

    logging.info("Frage LLM um eine Entscheidung an...")
    prompt_template = ""
    try:
        with open("llm_prompt_template.txt", "r") as f:
            prompt_template = f.read()
    except FileNotFoundError:
        logging.critical("llm_prompt_template.txt nicht gefunden. Kann nicht fortfahren.")
        return None

    full_prompt = prompt_template.format(data=json.dumps(context_data, indent=2))
    
    payload = {
        "model": config.get("LLM", "model"),
        "prompt": full_prompt,
        "stream": False,
        "options": {
            "temperature": 0.2, # Pragmatische, wiederholbare Antworten
        }
    }

    try:
        response = requests.post(config.get("LLM", "api_url"), json=payload, timeout=30)
        response.raise_for_status()
        
        response_json = response.json()
        llm_decision = response_json.get("response", "").strip()
        
        logging.info(f"LLM-Entscheidung erhalten: '{llm_decision}'")
        return llm_decision
    except requests.exceptions.RequestException as e:
        logging.error(f"Fehler bei der Anfrage an die LLM-API: {e}")
        return None
    except json.JSONDecodeError:
        logging.error(f"Ungültige JSON-Antwort von LLM-API: {response.text}")
        return None


def execute_action(command: str, ssh_manager: SSHManager):
    """Führt den vom LLM zurückgegebenen Befehl aus."""
    if not command:
        logging.info("Keine Aktion auszuführen.")
        return

    logging.info(f"Führe Aktion aus: '{command}'")
    
    parts = command.split(":", 1)
    action = parts[0].strip().upper()
    
    at_command = None
    
    if action == "WAIT":
        logging.info(f"Aktion ist 'WAIT', keine Ausführung nötig.")
        return
    elif action == "RESTART_MODEM":
        # Ein "weicher" Neustart des Modems
        at_command = "gsmctl -A 'AT+CFUN=1,1'"
    elif action == "RESET_BANDS":
        at_command = "gsmctl -A 'AT+QNWPREFCFG=\"lte_band\",1:3:7:8:20:28:38:40 && gsmctl -A 'AT+QNWPREFCFG=\"nr5g_band\",1:3:7:8:20:28:38:40:77:78'" # Alle gängigen Bänder freigeben
    elif action == "SET_LTE_BANDS" and len(parts) > 1:
        bands = parts[1].strip()
        at_command = f"gsmctl -A 'AT+QNWPREFCFG=\"lte_band\",{bands}'"
    elif action == "SET_NR5G_BANDS" and len(parts) > 1:
        bands = parts[1].strip()
        at_command = f"gsmctl -A 'AT+QNWPREFCFG=\"nr5g_band\",{bands}'"
    elif action == "FULL_SCAN":
        logging.info("Führe vollständigen Netzwerkscan durch...")
        # Prozess: Verbindung stoppen, scannen, starten
        ssh_manager.execute_command("ubus call network.interface.mobile down")
        time.sleep(5)
        scan_output, _ = ssh_manager.execute_command("gsmctl -A 'AT+QSCAN=3,1'")
        logging.info(f"Scan-Ergebnis:\n{scan_output}")
        ssh_manager.execute_command("ubus call network.interface.mobile up")
        return # Spezifische Aktion, kein generisches AT-Kommando
    else:
        logging.warning(f"Unbekannter oder unvollständiger Befehl erhalten: '{command}'")
        return

    if at_command:
        output, error = ssh_manager.execute_command(at_command)
        if error:
            logging.error(f"Fehler bei Ausführung von '{at_command}': {error}")
        else:
            logging.info(f"Befehl '{at_command}' erfolgreich ausgeführt. Output: {output}")


def main():
    """Haupt-Watchdog-Schleife."""
    config = configparser.ConfigParser()
    config.read('config.ini')

    log_file = config.get("WATCHDOG", "log_file", fallback="watchdog.log")
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[logging.FileHandler(log_file), logging.StreamHandler()]
    )

    check_interval = config.getint("WATCHDOG", "check_interval", fallback=60)
    max_actions = config.getint("WATCHDOG", "max_actions_per_hour", fallback=10)
    override_flag = config.get("WATCHDOG", "manual_override_flag")
    
    action_timestamps = deque()

    logging.info("Smart Watchdog wird gestartet...")

    while True:
        try:
            # 1. Manuelle Sperre prüfen
            if os.path.exists(override_flag):
                logging.warning(f"Manuelle Sperrdatei '{override_flag}' gefunden. Pausiere Aktionen.")
                time.sleep(check_interval)
                continue

            # 2. Rate Limiting prüfen
            now = datetime.now()
            one_hour_ago = now - timedelta(hours=1)
            # Alte Timestamps entfernen
            while action_timestamps and action_timestamps[0] < one_hour_ago:
                action_timestamps.popleft()

            if len(action_timestamps) >= max_actions:
                logging.warning(f"Aktionslimit erreicht ({max_actions}/Stunde). Pausiere...")
                time.sleep(check_interval)
                continue

            # 3. Kernlogik
            with SSHManager(
                config.get("ROUTER", "host"),
                config.get("ROUTER", "user"),
                config.get("ROUTER", "password")
            ) as ssh:
            
                if not ssh.client:
                    logging.error("SSH-Verbindung konnte nicht aufgebaut werden. Nächster Versuch in 60s.")
                    time.sleep(60)
                    continue

                modem_data = get_modem_data(ssh)
                logging.info(f"Aktuelle Daten: {json.dumps(modem_data)}")

                # Fallback, wenn LLM nicht antwortet oder keine Aktion vorschlägt
                decision = query_llm(modem_data, config) or "WAIT: 5"
                
                # Nur "echte" Aktionen loggen und ausführen
                if not decision.upper().startswith("WAIT"):
                    action_timestamps.append(now)
                    execute_action(decision, ssh)

            logging.info(f"Warte {check_interval} Sekunden bis zur nächsten Prüfung.")
            time.sleep(check_interval)

        except KeyboardInterrupt:
            logging.info("Watchdog wird beendet.")
            break
        except Exception as e:
            logging.critical(f"Ein unerwarteter Fehler ist in der Hauptschleife aufgetreten: {e}", exc_info=True)
            time.sleep(60) # Warte eine Minute vor dem nächsten Versuch

if __name__ == "__main__":
    main() 