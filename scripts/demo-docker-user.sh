#!/bin/bash
# Intentional DOCKER-USER for PiHerder public demo.
# Inbound: Cloudflare + optional admin. Outbound: DNS to chosen resolvers + HTTPS to CF only.
#
# Usage:
#   sudo WAN=eth0 COMPOSE_NET=172.18.0.0/16 ADMIN_IP=41.193.110.155 ./demo-docker-user.sh
#   sudo ./demo-docker-user.sh   # defaults: eth0, 172.18.0.0/16, no admin IP
#
# Refresh CF list: https://www.cloudflare.com/ips-v4

set -eu

WAN="${WAN:-eth0}"
COMPOSE_NET="${COMPOSE_NET:-172.18.0.0/16}"
ADMIN_IP="${ADMIN_IP:-}"

if [ "$(id -u)" -ne 0 ]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi

iptables -N DOCKER-USER 2>/dev/null || true
iptables -F DOCKER-USER

# 1) Return path for allowed connections
iptables -A DOCKER-USER -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN

# 2) Outbound DNS to chosen resolvers only
for dns in 1.1.1.1 8.8.8.8 9.9.9.9; do
  iptables -A DOCKER-USER -s "$COMPOSE_NET" -o "$WAN" -d "$dns" -p udp --dport 53 -j RETURN
  iptables -A DOCKER-USER -s "$COMPOSE_NET" -o "$WAN" -d "$dns" -p tcp --dport 53 -j RETURN
done

# 3) Outbound HTTPS only to Cloudflare (Turnstile siteverify)
# shellcheck disable=SC2043
for cidr in \
  173.245.48.0/20 \
  103.21.244.0/22 \
  103.22.200.0/22 \
  103.31.4.0/22 \
  141.101.64.0/18 \
  108.162.192.0/18 \
  190.93.240.0/20 \
  188.114.96.0/20 \
  197.234.240.0/22 \
  198.41.128.0/17 \
  162.158.0.0/15 \
  104.16.0.0/13 \
  104.24.0.0/14 \
  172.64.0.0/13 \
  131.0.72.0/22
do
  iptables -A DOCKER-USER -s "$COMPOSE_NET" -o "$WAN" -d "$cidr" -p tcp --dport 443 -j RETURN
done

# 4) No other container egress to the internet
iptables -A DOCKER-USER -s "$COMPOSE_NET" -o "$WAN" -j DROP

# 5) Inbound Cloudflare -> origin 80/443
for cidr in \
  173.245.48.0/20 \
  103.21.244.0/22 \
  103.22.200.0/22 \
  103.31.4.0/22 \
  141.101.64.0/18 \
  108.162.192.0/18 \
  190.93.240.0/20 \
  188.114.96.0/20 \
  197.234.240.0/22 \
  198.41.128.0/17 \
  162.158.0.0/15 \
  104.16.0.0/13 \
  104.24.0.0/14 \
  172.64.0.0/13 \
  131.0.72.0/22
do
  iptables -A DOCKER-USER -s "$cidr" -i "$WAN" -p tcp --dport 443 -j RETURN
  iptables -A DOCKER-USER -s "$cidr" -i "$WAN" -p tcp --dport 80 -j RETURN
done

# 6) Optional admin IP
if [ -n "$ADMIN_IP" ]; then
  iptables -A DOCKER-USER -s "${ADMIN_IP}/32" -i "$WAN" -j RETURN
fi

# 7) Drop remaining WAN -> Docker
iptables -A DOCKER-USER -i "$WAN" -j DROP

# 8) Anything else -> Docker default handling
iptables -A DOCKER-USER -j RETURN

echo "DOCKER-USER OK  COMPOSE_NET=$COMPOSE_NET WAN=$WAN ADMIN_IP=${ADMIN_IP:-none}"
echo "---- rules ----"
iptables -S DOCKER-USER
echo "---- end ----"
