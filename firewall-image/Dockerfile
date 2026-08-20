FROM debian:bookworm-slim

RUN apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        firewalld \
        dbus \
        # netbase provides /etc/protocols — without it firewalld cannot resolve
        # protocol value="tcp|udp" in rich rules (INVALID_PROTOCOL, API 500s on
        # port-less protocol blocks); bookworm-slim does not ship it.
        netbase \
        nftables \
        iproute2 \
        iputils-ping \
        python3 \
        python3-pip \
        procps \
    && pip3 install --break-system-packages fastapi uvicorn[standard] \
    && apt-get clean && rm -rf /var/lib/apt/lists/* \
    && rm -f /usr/lib/firewalld/services/ipsec.xml \
    && rm -f /usr/lib/firewalld/services/docker-swarm.xml \
    && rm -f /usr/lib/firewalld/services/gre.xml

COPY fw-api.py /opt/fw-api.py
COPY entrypoint.sh /opt/entrypoint.sh
RUN chmod +x /opt/entrypoint.sh

EXPOSE 8080

CMD ["/opt/entrypoint.sh"]
