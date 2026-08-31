# DELIBERATELY-VULNERABLE lab host image (the demo's "one vulnerable PC").
#
# Identical in behaviour to weblab-image (nginx HTTP 80 + HTTPS 443 + socat UDP
# echo + lynx) but built on an OLD nginx:alpine base — Alpine 3.14, which ships
# OpenSSL 1.1.1 (end-of-life; genuinely carries real CVEs, e.g. the CRITICAL
# CVE-2024-5535). The vulnerability scanner reports these against this node, so
# it is flagged "(vulnerable)" in the topology.
#
# The LLM can FIX it live via the run_command tool: pointing this container's
# apk repositories at a current Alpine branch and running `apk upgrade` moves
# OpenSSL to 3.x, which clears the flag on the next scan — nothing simulated,
# the running container's package really changes. See app/prompts.py.
#
# Only pc1a uses this image; every other web host uses the current weblab.
FROM nginx:1.20-alpine

RUN apk add --no-cache openssl socat lynx curl \
    && mkdir -p /etc/nginx/certs \
    && openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
        -keyout /etc/nginx/certs/server.key \
        -out   /etc/nginx/certs/server.crt \
        -subj "/CN=networklab.demo" \
    && rm -rf /var/cache/apk/*

COPY default.conf /etc/nginx/conf.d/default.conf

EXPOSE 80 443
