Use **split DNS** on the host: send `.internal` lookups to kind and keep normal DNS for everything else.

Your cluster already exposes DNS at **`127.0.0.1:1053`**, as configured in the [kind profile](https://github.com/Sebastian-Nowaczyk-Elektrorecykling/k8s-level-base-elektro/blob/main/docs/kind.md). On native Debian with Docker, it returns the reachable kind node IP.

First verify it and identify your host’s resolver:

```bash
# Install dnsutils if dig is missing.
dig @127.0.0.1 -p 1053 auth.internal +short

systemctl is-active NetworkManager
systemctl is-active systemd-resolved
```

**If NetworkManager is active and systemd-resolved is inactive**, configure NetworkManager’s dnsmasq plugin:

```bash
sudo apt-get install -y dnsmasq-base
sudo mkdir -p /etc/NetworkManager/conf.d /etc/NetworkManager/dnsmasq.d

sudo tee /etc/NetworkManager/conf.d/90-kind-dns.conf >/dev/null <<'EOF'
[main]
dns=dnsmasq
EOF

sudo tee /etc/NetworkManager/dnsmasq.d/kind.conf >/dev/null <<'EOF'
server=/internal/127.0.0.1#1053
rebind-domain-ok=/internal/
EOF

sudo nmcli general reload
```

This persists across reboots. NetworkManager supplies your normal upstream DNS servers; the additional rule forwards `internal` and its subdomains to kind. [NetworkManager documentation](https://networkmanager.dev/docs/api/latest/NetworkManager.conf.html), [dnsmasq forwarding syntax](https://thekelleys.org.uk/dnsmasq/docs/dnsmasq-man.html).

**If systemd-resolved is active**, keep it and configure a DNS route on the Docker bridge. This uses the node’s port 53 directly:

```bash
kind_ip=$(docker inspect \
  -f '{{(index .NetworkSettings.Networks "kind").IPAddress}}' \
  elektro-test-control-plane)

kind_link=$(ip -j route get "$kind_ip" |
  python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["dev"])')

sudo resolvectl dns "$kind_link" "$kind_ip"
sudo resolvectl domain "$kind_link" '~internal'
sudo resolvectl default-route "$kind_link" no
sudo resolvectl flush-caches
```

These are runtime settings: reapply after reboot or cluster recreation. Undo with `sudo resolvectl revert "$kind_link"`. [Debian’s resolvectl documentation](https://manpages.debian.org/trixie/systemd-resolved/resolvectl.1.en.html).

Then test host resolution and, once the authentication add-on is deployed, HTTPS:

```bash
getent ahostsv4 auth.internal

curl --noproxy '*' \
  --cacert .state/elektro-test/ca.crt \
  https://auth.internal/
```

This selects the **test cluster’s `.internal` names on this host**. If your LAN/VPN also has an `.internal` DNS route, that competing route needs resolving to avoid production/test answers racing.

If both services report inactive, paste `cat /etc/resolv.conf` and `readlink -f /etc/resolv.conf`; the setup then depends on which service manages that file.
