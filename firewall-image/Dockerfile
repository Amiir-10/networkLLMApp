FROM debian:bookworm-slim

RUN apt-get update -qq && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        firewalld \
        dbus \
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
