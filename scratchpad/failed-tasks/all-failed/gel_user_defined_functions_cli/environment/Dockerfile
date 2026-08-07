FROM ubuntu:24.04

ARG POCHI_VERSION
ARG GEL_MAJOR=6
ARG GEL_VERSION=6.11

# Connection context for the bundled local Gel instance (no secrets: dev-mode trust auth).
ENV GEL_HOST=127.0.0.1
ENV GEL_PORT=5656
ENV GEL_USER=admin
ENV GEL_BRANCH=main
ENV GEL_CLIENT_TLS_SECURITY=insecure

# Base tooling. NOTE: python3-pytest is deliberately NOT installed - the distro pytest is
# too old for pytest-json-ctrf and aborts the whole suite with an INTERNALERROR.
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates gnupg git python3 python3-pip bash ripgrep jq procps && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest pytest-json-ctrf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Gel server 6.11 and the Gel CLI from the official apt repository.
RUN mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt $(grep VERSION_CODENAME= /etc/os-release | cut -d= -f2) main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        "gel-${GEL_MAJOR}=${GEL_VERSION}*" && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 6.11 local instance with an initialized project at /home/user/functions-lab holding a Carrier type, two carrier rows, one applied migration and a seed data file, for building user-defined EdgeQL functions through the gel CLI."

# The server binary name varies across images and Gel majors - resolve it defensively.
RUN cat >/usr/local/bin/gel-resolve-server <<'EOS' && chmod 0755 /usr/local/bin/gel-resolve-server
#!/bin/bash
for cand in gel-server gel-server-7 gel-server-6; do
  if command -v "$cand" >/dev/null 2>&1; then
    command -v "$cand"
    exit 0
  fi
done
fallback=$(ls /usr/bin/gel-server* 2>/dev/null | head -n 1)
if [ -n "$fallback" ]; then
  echo "$fallback"
  exit 0
fi
echo "FATAL: no gel-server binary found (tried gel-server, gel-server-7, gel-server-6, /usr/bin/gel-server*)" >&2
exit 1
EOS

# Idempotent starter. gel-server refuses to run as root and never uses the default
# /run/gel runstate directory here: an explicit, pre-created, chowned one is passed.
RUN cat >/usr/local/bin/gel-start <<'EOS' && chmod 0755 /usr/local/bin/gel-start
#!/bin/bash
export GEL_HOST=127.0.0.1
export GEL_PORT=5656
export GEL_USER=admin
export GEL_BRANCH=main
export GEL_CLIENT_TLS_SECURITY=insecure

if gel query "select 1" >/dev/null 2>&1; then
  exit 0
fi

SERVER_BIN=$(/usr/local/bin/gel-resolve-server) || exit 1
mkdir -p /var/lib/gel/data /var/lib/gel/run
chown -R gel:gel /var/lib/gel/data /var/lib/gel/run

nohup su gel -s /bin/bash -c "$SERVER_BIN \
    --data-dir=/var/lib/gel/data \
    --runstate-dir=/var/lib/gel/run \
    --port=5656 \
    --bind-address=127.0.0.1 \
    --security=insecure_dev_mode \
    --default-auth-method=Trust \
    --tls-cert-mode=generate_self_signed" >>/var/log/gel-server.log 2>&1 &

for _ in $(seq 1 180); do
  if gel query "select 1" >/dev/null 2>&1; then
    exit 0
  fi
  sleep 1
done

echo "FATAL: the local Gel instance did not become ready in time" >&2
tail -n 40 /var/log/gel-server.log >&2
exit 1
EOS

RUN mkdir -p /home/user && chmod 0755 /home/user

# Project skeleton: manifest, current schema (carriers only) and the seed data file.
RUN mkdir -p /home/user/functions-lab/dbschema /home/user/functions-lab/seed && \
    cat >/home/user/functions-lab/gel.toml <<'EOS'
[instance]
server-version = "6.11"
EOS

RUN cat >/home/user/functions-lab/dbschema/default.gel <<'EOS'
module default {
  type Carrier {
    required name: str {
      constraint exclusive;
    }
    required hub_code: str;
  }
}
EOS

RUN cat >/home/user/functions-lab/seed/data.json <<'EOS'
{
  "parcels": [
    {"sku": "SKU-1001", "weight_grams": 1200, "tier": "Express", "insured": false, "handling_hint": "fragile", "carrier": "Northwind"},
    {"sku": "SKU-1002", "weight_grams": 400, "tier": "Ground", "insured": true, "handling_hint": null, "carrier": "Northwind"},
    {"sku": "SKU-1003", "weight_grams": 2000, "tier": "Overnight", "insured": false, "handling_hint": null, "carrier": "Halcyon"},
    {"sku": "SKU-1004", "weight_grams": 50, "tier": "Ground", "insured": false, "handling_hint": "sample", "carrier": "Halcyon"}
  ],
  "shipments": [
    {"code": "SHP-9001", "parcels": ["SKU-1001", "SKU-1002", "SKU-1003"]},
    {"code": "SHP-9002", "parcels": []}
  ]
}
EOS

# Bootstrap the instance, apply the carrier migration, load the two carrier rows,
# then shut the server down cleanly so the baked data directory is consistent.
RUN SERVER_BIN=$(/usr/local/bin/gel-resolve-server) && \
    mkdir -p /var/lib/gel/data /var/lib/gel/run && \
    chown -R gel:gel /var/lib/gel/data /var/lib/gel/run && \
    su gel -s /bin/bash -c "$SERVER_BIN \
        --data-dir=/var/lib/gel/data \
        --runstate-dir=/var/lib/gel/run \
        --bootstrap-only \
        --security=insecure_dev_mode \
        --default-auth-method=Trust \
        --tls-cert-mode=generate_self_signed" && \
    /usr/local/bin/gel-start && \
    cd /home/user/functions-lab && \
    gel migration create --non-interactive && \
    gel migrate && \
    gel query "insert Carrier { name := 'Northwind', hub_code := 'PDX' }" && \
    gel query "insert Carrier { name := 'Halcyon', hub_code := 'SEA' }" && \
    gel migration status && \
    pkill -TERM -u gel -f gel-server; \
    for _ in $(seq 1 60); do \
        if ! pgrep -u gel -f gel-server >/dev/null 2>&1; then break; fi; \
        sleep 1; \
    done; \
    test -d /var/lib/gel/data

CMD ["/bin/bash", "-c", "/usr/local/bin/gel-start >>/var/log/gel-start.log 2>&1; trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep
#     (python3-pytest intentionally omitted; pytest comes from pip3 so pytest-json-ctrf works)
#   - extra apt packages: gnupg (apt keyring), jq, procps
#   - always required pip3 packages: pytest, pytest-json-ctrf
#   - Gel 6.11 server (gel-server-6) plus the Gel CLI, installed from packages.geldata.com
#   - /usr/local/bin/gel-resolve-server: defensive gel-server binary resolution
#   - /usr/local/bin/gel-start: idempotent server start with an explicit chowned --runstate-dir
#   - /var/lib/gel/data: bootstrapped instance (branch main, trust auth, insecure dev mode)
#   - /home/user/functions-lab: initialized Gel project (gel.toml, dbschema/default.gel with the
#     Carrier type, dbschema/migrations/ with the applied initial migration, seed/data.json)
#   - two Carrier rows preloaded: Northwind/PDX and Halcyon/SEA

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
