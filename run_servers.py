import os
import subprocess
import time
import re
import sys

# Reconfigure stdout to use utf-8 to avoid console encoding issues on Windows
sys.stdout.reconfigure(encoding='utf-8')

def kill_process_on_port(port):
    try:
        output = subprocess.check_output(f'netstat -ano | findstr :{port}', shell=True).decode('utf-8')
        lines = output.strip().split('\n')
        pids = set()
        for line in lines:
            parts = re.split(r'\s+', line.strip())
            if len(parts) >= 5 and "LISTENING" in parts:
                pids.add(parts[4])
        
        for pid in pids:
            print(f"Port {port} is occupied by PID {pid}. Terminating process...")
            subprocess.run(f'taskkill /F /PID {pid}', shell=True)
            time.sleep(1)
    except Exception:
        print(f"No active process found on port {port}.")

# 1. Kill any existing processes on 5000 and 3000 to avoid conflicts
print("Cleaning up conflicting ports...")
kill_process_on_port(5000)
kill_process_on_port(3000)

# 2. Start Baileys Node.js Service (port 3000)
print("\nStarting Baileys Node.js service on port 3000...")
base_dir = os.path.dirname(os.path.abspath(__file__))
baileys_dir = os.path.join(base_dir, "baileys-service")
baileys_log = open(os.path.join(baileys_dir, "baileys.log"), "w", encoding="utf-8")
baileys_proc = subprocess.Popen(
    ["node", "index.js"],
    cwd=baileys_dir,
    stdout=baileys_log,
    stderr=baileys_log,
    shell=True
)

# 3. Start Flask Web App (port 5000)
print("Starting Flask Web App on port 5000...")
flask_dir = os.path.join(base_dir, "flask-app")
flask_log = open(os.path.join(flask_dir, "flask.log"), "w", encoding="utf-8")
env = os.environ.copy()
env["PYTHONIOENCODING"] = "utf-8"
flask_proc = subprocess.Popen(
    ["python", "app.py"],
    cwd=flask_dir,
    stdout=flask_log,
    stderr=flask_log,
    env=env,
    shell=True
)

# 4. Wait a few seconds and print the logs to check startup status
print("Waiting for services to initialize...")
time.sleep(5)

print("\n--- Baileys Service Log ---")
try:
    with open(os.path.join(baileys_dir, "baileys.log"), "r", encoding="utf-8") as f:
        print(f.read())
except Exception as e:
    print(f"Could not read Baileys log: {e}")

print("\n--- Flask Web App Log ---")
try:
    with open(os.path.join(flask_dir, "flask.log"), "r", encoding="utf-8") as f:
        print(f.read())
except Exception as e:
    print(f"Could not read Flask log: {e}")

print("\nServices started successfully.")
print("Flask Web App: http://127.0.0.1:5000")
print("Baileys Service: http://localhost:3000")
print("Press Ctrl+C to terminate services...")

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nTerminating services...")
    try:
        baileys_proc.terminate()
    except Exception:
        pass
    try:
        flask_proc.terminate()
    except Exception:
        pass
    print("Services stopped.")

