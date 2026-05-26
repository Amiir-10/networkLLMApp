#!/bin/bash
set -e

mkdir -p /run/dbus
dbus-daemon --system --fork

sleep 1

firewalld --nofork --nopid &
FW_PID=$!

for i in $(seq 1 30); do
    if firewall-cmd --state 2>/dev/null | grep -q running; then
        break
    fi
    sleep 0.5
done

firewall-cmd --zone=trusted --add-interface=eth0 --permanent 2>/dev/null || true
firewall-cmd --set-default-zone=public
firewall-cmd --zone=public --add-interface=eth1 --permanent 2>/dev/null || true
firewall-cmd --zone=public --add-interface=eth2 --permanent 2>/dev/null || true
firewall-cmd --zone=public --add-port=8080/tcp --permanent 2>/dev/null || true

firewall-cmd --new-policy=fwd-filter --permanent 2>/dev/null || true
firewall-cmd --policy=fwd-filter --set-target=CONTINUE --permanent 2>/dev/null || true
firewall-cmd --policy=fwd-filter --add-ingress-zone=public --permanent 2>/dev/null || true
firewall-cmd --policy=fwd-filter --add-egress-zone=public --permanent 2>/dev/null || true

firewall-cmd --reload

echo "firewalld ready"

uvicorn fw-api:app --host 0.0.0.0 --port 8080 --app-dir /opt &

wait $FW_PID
