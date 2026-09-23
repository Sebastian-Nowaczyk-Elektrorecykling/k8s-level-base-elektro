sudo apt-get update
sudo apt-get install -y curl ca-certificates

(
  set -eu
  kind_tmp=$(mktemp -d)
  trap 'rm -rf "$kind_tmp"' EXIT
  cd "$kind_tmp"

  kind_asset="kind-linux-$(dpkg --print-architecture)"
  kind_url="https://github.com/kubernetes-sigs/kind/releases/download/v0.33.0"

  curl -fsSLO "$kind_url/$kind_asset"
  curl -fsSLO "$kind_url/$kind_asset.sha256sum"
  sha256sum --check "$kind_asset.sha256sum"

  sudo install -m 0755 "$kind_asset" /usr/local/bin/kind
)

export PATH="/usr/local/bin:$PATH"
hash -r
kind version
