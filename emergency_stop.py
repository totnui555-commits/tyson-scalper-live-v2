from pathlib import Path
from utils import load_config

cfg = load_config()
p = Path(cfg["kill_switch_file"])
p.touch(exist_ok=True)
print(f"KILL SWITCH ACTIVE: {p.resolve()}")
print("New live entries are blocked. Existing positions remain protected by their broker-side SL/TP; manage them in MT5 if needed.")
