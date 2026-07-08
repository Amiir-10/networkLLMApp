# Lab web/host image for the networkLLMApp demo.
#
# Extends the stock nginx:alpine PC image with two things the base image cannot
# do, so the firewall demo can block real TCP *and* real UDP services:
#   1. Real HTTPS (TCP 443) — a self-signed cert is generated at BUILD time
#      (build host has internet for `apk add openssl`; the running lab does not),
#      and nginx is reconfigured to listen on both 80 (HTTP) and 443 (HTTPS).
#   2. A persistent UDP echo responder — `socat` (added here; the running lab
#      has no internet to apk it in) is launched per-scenario via the node's
#      `launch:` field as `socat UDP-LISTEN:9999,fork,reuseaddr EXEC:/bin/cat`.
#      busybox `nc -lk` dies after a single datagram, so it is NOT usable for a
#      repeatable live demo — socat's fork model handles every datagram.
#
# nginx still runs as PID 1 (inherited CMD `nginx -g 'daemon off;'`), so the
# scenario keeps idle=False and the UDP service starts via launch after L3 is up.
FROM nginx:alpine

RUN apk add --no-cache openssl socat \
    && mkdir -p /etc/nginx/certs \
    && openssl req -x509 -nodes -newkey rsa:2048 -days 3650 \
        -keyout /etc/nginx/certs/server.key \
        -out   /etc/nginx/certs/server.crt \
        -subj "/CN=networklab.demo" \
    && rm -rf /var/cache/apk/*

COPY default.conf /etc/nginx/conf.d/default.conf

EXPOSE 80 443
