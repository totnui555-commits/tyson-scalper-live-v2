from __future__ import annotations

import argparse
import yaml
import broker_mt5 as broker
from utils import load_config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-whitelist", action="store_true", help="Write current MT5 login/server into config.yaml")
    args = ap.parse_args()

    cfg = load_config()
    broker.connect()
    try:
        a = broker.account_info()
        print(f"Current MT5 account: login={a.login} server={a.server} currency={a.currency}")
        if args.write_whitelist:
            cfg.setdefault("account", {})["allowed_logins"] = [int(a.login)]
            cfg["account"]["allowed_servers"] = [str(a.server)]
            with open("config.yaml", "w", encoding="utf-8") as f:
                yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
            print("Updated config.yaml account whitelist. Live trading is NOT armed by this script.")
    finally:
        broker.shutdown()


if __name__ == "__main__":
    main()
