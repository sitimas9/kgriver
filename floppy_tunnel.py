#!/usr/bin/env python3
"""Launch N loopback CONNECT forwarders over the floppydata residential pool.

Each forwarder = one sticky-ish identity bound to a distinct credential line.
Writes /tmp/floppy_tunnels.json mapping listen_port -> upstream user.
"""
import json
import os
import random
import subprocess
import sys

POOL = os.path.expanduser("~/floppydata_live.txt")
FWD = os.path.expanduser(
    "~/.hermes/skills/security/proxy-testing/scripts/http_fwd.py"
)
STATE = "/tmp/floppy_tunnels.json"


def parse(line: str):
    # http://USER:PASS@HOST:PORT
    rest = line.split("://", 1)[1]
    creds, hostport = rest.rsplit("@", 1)
    user, pw = creds.split(":", 1)
    host, port = hostport.split(":")
    return user, pw, host, int(port)


def main():
    base_port = int(sys.argv[1]) if len(sys.argv) > 1 else 9130
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    lines = [l.strip() for l in open(POOL) if l.strip().startswith("http")]
    random.shuffle(lines)
    picked = lines[:count]

    mapping = {}
    for i, line in enumerate(picked):
        user, pw, host, port = parse(line)
        lp = base_port + i
        subprocess.Popen(
            [sys.executable, FWD, str(lp), host, str(port), user, pw],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        mapping[str(lp)] = {"up_user": user, "host": host, "port": port}
        print(f"[tunnel] 127.0.0.1:{lp} -> {host}:{port} ({user[:14]}...)")

    json.dump(mapping, open(STATE, "w"), indent=1)
    print(f"[state] {STATE} ({len(mapping)} tunnels)")


if __name__ == "__main__":
    main()
