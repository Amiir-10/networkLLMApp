# WAN-Interconnect gateway image for the networkLLMApp lab.
#
# The lab's data plane needs a REAL internet uplink that still traverses the
# firewalls (supervisor request 2026-08-25: containers must reach the public
# internet for apt/apk installs and the Browser tab, AND public sites must be
# blockable). The stock alpine switch image cannot do this: NAT needs iptables,
# which base alpine does not ship and the running lab cannot apk-install
# (chicken-and-egg: no internet until this node works).
#
# The node bridges the router uplinks as before (pure L2 between the sites),
# but additionally gets an IP on the bridge, forwards, and MASQUERADEs
# internet-bound traffic out its management interface (eth0) — where docker's
# own NAT takes it to the real internet. So the path is:
#   pc -> switch -> firewall -> router -> WAN-Interconnect (SNAT) -> host -> internet
# and every hop up to the WAN node is firewalled, so DROP rules genuinely kill
# real-internet traffic.
FROM alpine:3.20

RUN apk add --no-cache iptables

CMD ["sleep", "infinity"]
