FROM ubuntu:24.04

ARG POCHI_VERSION
ARG GEL_MAJOR=6
ARG GEL_PYTHON_VERSION=3.1.0

# Base tooling, Pochi CLI, the Gel apt repository (server + CLI) and the Python deps
# needed by the initial-state / final-state test suites.
# NOTE: `python3-pytest` is deliberately NOT installed: the distro pytest is too old for
# `pytest-json-ctrf`, which aborts the whole run with an INTERNALERROR. pytest comes from pip.
RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip bash ripgrep gnupg \
        procps psmisc lsb-release && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo "deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main" \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gel-${GEL_MAJOR} && \
    pip3 install --break-system-packages \
        pytest pytest-json-ctrf gel==${GEL_PYTHON_VERSION} && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Connection settings shared by the `gel` CLI and the Python client. The local server runs
# with `insecure_dev_mode` (Trust auth + self-signed certificate), so no password is needed.
ENV GEL_DSN="gel://admin@127.0.0.1:5656/main"
ENV GEL_CLIENT_TLS_SECURITY="insecure"
ENV GEL_WAIT_UNTIL_AVAILABLE="60s"
ENV GEL_SERVER_COMPILER_POOL_SIZE="2"

RUN mkdir -p /home/user && chmod 0755 /home/user

# `gel-server` refuses to run as root, so it always runs as this dedicated account.
RUN id -u gel >/dev/null 2>&1 || \
        useradd --system --create-home --home-dir /var/lib/gel --shell /bin/bash gel

RUN mkdir -p /var/lib/gel/data /var/lib/gel/run && \
    touch /var/log/gel-server.log && \
    chown -R gel:gel /var/lib/gel /var/log/gel-server.log && \
    chmod 0700 /var/lib/gel/data

# Idempotent launcher for the local Gel server.
#   * the server binary is resolved defensively (its name differs per install method/version)
#   * `--runstate-dir` is always passed explicitly at a path owned by the server user,
#     because the default (/run/gel) is root-owned and breaks non-root servers
RUN cat >/usr/local/bin/gel-start.sh <<'GELSTART'
#!/usr/bin/env bash
# Start the local Gel server used by the ledger project. Safe to run repeatedly.

LEDGER_DATA_DIR="${LEDGER_DATA_DIR:-/var/lib/gel/data}"
LEDGER_RUN_DIR="${LEDGER_RUN_DIR:-/var/lib/gel/run}"
LEDGER_LOG_FILE="${LEDGER_LOG_FILE:-/var/log/gel-server.log}"
LEDGER_PID_FILE="${LEDGER_PID_FILE:-/var/lib/gel/run/gel-server.pid}"
LEDGER_PORT="${LEDGER_PORT:-5656}"
LEDGER_OS_USER="${LEDGER_OS_USER:-gel}"

port_open() {
  python3 -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1', $LEDGER_PORT))==0 else 1)"
}

resolve_server_cmd() {
  local candidate
  for candidate in gel-server gel-server-6 gel-server-7 gel-server-8; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return 0
    fi
  done
  candidate="$(ls /usr/bin/gel-server* 2>/dev/null | head -n 1)"
  if [ -n "$candidate" ]; then
    echo "$candidate"
    return 0
  fi
  candidate="$(ls /usr/lib/*/gel-server-*/bin/gel-server 2>/dev/null | head -n 1)"
  if [ -n "$candidate" ]; then
    echo "$candidate"
    return 0
  fi
  if [ -x /usr/local/bin/docker-entrypoint.sh ]; then
    echo "/usr/local/bin/docker-entrypoint.sh server"
    return 0
  fi
  return 1
}

if port_open; then
  echo "gel-start: a server is already listening on 127.0.0.1:${LEDGER_PORT}"
  exit 0
fi

SERVER_CMD="$(resolve_server_cmd)"
if [ -z "$SERVER_CMD" ]; then
  echo "gel-start: FATAL - no Gel server found. Tried gel-server, gel-server-6, gel-server-7," >&2
  echo "gel-server-8, /usr/bin/gel-server*, /usr/lib/*/gel-server-*/bin/gel-server and" >&2
  echo "/usr/local/bin/docker-entrypoint.sh server." >&2
  exit 1
fi

SERVER_ARGS="--data-dir=${LEDGER_DATA_DIR} --runstate-dir=${LEDGER_RUN_DIR} --port=${LEDGER_PORT} --bind-address=127.0.0.1 --security=insecure_dev_mode --tls-cert-mode=generate_self_signed"

mkdir -p "$LEDGER_DATA_DIR" "$LEDGER_RUN_DIR"
touch "$LEDGER_LOG_FILE" 2>/dev/null

if [ "$(id -u)" = "0" ]; then
  chown -R "${LEDGER_OS_USER}:${LEDGER_OS_USER}" "$LEDGER_DATA_DIR" "$LEDGER_RUN_DIR" "$LEDGER_LOG_FILE"
  chmod 0700 "$LEDGER_DATA_DIR"
  su "$LEDGER_OS_USER" -s /bin/bash -c "nohup ${SERVER_CMD} ${SERVER_ARGS} >>${LEDGER_LOG_FILE} 2>&1 & echo \$! >${LEDGER_PID_FILE}"
elif [ "$(id -un)" = "$LEDGER_OS_USER" ]; then
  nohup ${SERVER_CMD} ${SERVER_ARGS} >>"${LEDGER_LOG_FILE}" 2>&1 &
  echo $! >"${LEDGER_PID_FILE}"
else
  echo "gel-start: FATAL - run this script as root or as the '${LEDGER_OS_USER}' user;" >&2
  echo "the Gel data directory is owned by '${LEDGER_OS_USER}' and the server cannot run as root." >&2
  exit 1
fi

echo "gel-start: launched ${SERVER_CMD} ${SERVER_ARGS}"
for _ in $(seq 1 300); do
  if port_open; then
    break
  fi
  if [ -f "$LEDGER_PID_FILE" ] && ! kill -0 "$(cat "$LEDGER_PID_FILE")" 2>/dev/null; then
    echo "gel-start: FATAL - the server process exited during startup." >&2
    tail -n 80 "${LEDGER_LOG_FILE}" >&2
    exit 1
  fi
  sleep 1
done

if ! port_open; then
  echo "gel-start: FATAL - the server did not start listening on 127.0.0.1:${LEDGER_PORT}." >&2
  tail -n 60 "${LEDGER_LOG_FILE}" >&2
  exit 1
fi

for _ in $(seq 1 60); do
  if gel query -F json 'select 1' >/dev/null 2>&1; then
    echo "gel-start: server is ready on 127.0.0.1:${LEDGER_PORT}"
    exit 0
  fi
  sleep 1
done

echo "gel-start: the port is open but queries are not answered yet; see ${LEDGER_LOG_FILE}"
exit 0
GELSTART

RUN cat >/usr/local/bin/gel-stop.sh <<'GELSTOP'
#!/usr/bin/env bash
# Gracefully stop the local Gel server started by gel-start.sh.

LEDGER_PID_FILE="${LEDGER_PID_FILE:-/var/lib/gel/run/gel-server.pid}"
LEDGER_PORT="${LEDGER_PORT:-5656}"

port_open() {
  python3 -c "import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(('127.0.0.1', $LEDGER_PORT))==0 else 1)"
}

if [ -f "$LEDGER_PID_FILE" ]; then
  kill -TERM "$(cat "$LEDGER_PID_FILE")" 2>/dev/null
fi

for _ in $(seq 1 60); do
  if ! port_open; then
    echo "gel-stop: server stopped"
    exit 0
  fi
  sleep 1
done

echo "gel-stop: the server is still listening on 127.0.0.1:${LEDGER_PORT}" >&2
exit 1
GELSTOP

RUN chmod 0755 /usr/local/bin/gel-start.sh /usr/local/bin/gel-stop.sh

# ---------------------------------------------------------------------------
# Initial project state: bare Account model + not-yet-implemented ledger API
# ---------------------------------------------------------------------------

RUN mkdir -p /home/user/ledger/dbschema && \
    cat >/home/user/ledger/gel.toml <<'GELTOML'
[instance]
server-version = "6.11"
GELTOML

RUN cat >/home/user/ledger/dbschema/default.gel <<'SCHEMA'
module default {
    type Account {
        required code: str;
        required opening_balance: decimal;
    }
}
SCHEMA

RUN cat >/home/user/ledger/ledger_api.py <<'LEDGERAPI'
"""Public API of the ledger service.

Every function receives an already configured ``gel.AsyncIOClient`` as its first
positional argument; all remaining arguments are keyword-only.  Monetary values are
handed in as ``decimal.Decimal`` and handed back as strings with exactly two
fractional digits.

Nothing here is implemented yet.
"""

from __future__ import annotations

from decimal import Decimal


class LedgerError(Exception):
    """Base class of every error raised by this module."""


class UnknownAccount(LedgerError):
    """No account exists for the given code."""


class DuplicateAccount(LedgerError):
    """An account with the given code already exists."""


class InvalidAmount(LedgerError):
    """The given amount is not a usable monetary value."""


class SameAccountTransfer(LedgerError):
    """Sender and recipient are the same account."""


class InsufficientFunds(LedgerError):
    """The sender does not hold enough money."""


async def create_account(client, *, code: str, opening_balance: Decimal) -> dict:
    """Create an account and return ``{"id", "code", "balance"}``."""
    raise NotImplementedError("create_account is not implemented yet")


async def create_transfer(
    client,
    *,
    sender: str,
    recipient: str,
    amount: Decimal,
    idempotency_key: str,
) -> dict:
    """Move money and return
    ``{"id", "sender", "recipient", "amount", "idempotency_key", "created"}``.
    """
    raise NotImplementedError("create_transfer is not implemented yet")


async def get_balance(client, *, account: str) -> str:
    """Return the current balance of ``account``."""
    raise NotImplementedError("get_balance is not implemented yet")


async def get_statement(client, *, account: str, limit: int = 50) -> list:
    """Return at most ``limit`` statement rows for ``account``, newest first."""
    raise NotImplementedError("get_statement is not implemented yet")
LEDGERAPI

# Bootstrap the instance, apply the baseline migration, seed the four accounts and keep a
# pristine copy of the baseline migration (its file name is content-hashed, so it is always
# resolved with a glob instead of being hardcoded).
RUN mkdir -p /opt/ledger-baseline/migrations && \
    /usr/local/bin/gel-start.sh && \
    cd /home/user/ledger && \
    gel migration create --non-interactive && \
    gel migrate && \
    gel query \
        "insert Account { code := 'ACC-1001', opening_balance := <decimal>'1000.00' }" \
        "insert Account { code := 'ACC-1002', opening_balance := <decimal>'500.00' }" \
        "insert Account { code := 'ACC-1003', opening_balance := <decimal>'250.50' }" \
        "insert Account { code := 'ACC-1004', opening_balance := <decimal>'0.00' }" && \
    gel query -F json "select count(Account)" && \
    gel migration status && \
    cp /home/user/ledger/dbschema/migrations/*.edgeql /opt/ledger-baseline/migrations/ && \
    ls -l /opt/ledger-baseline/migrations/ && \
    { /usr/local/bin/gel-stop.sh || true; }

RUN chown -R gel:gel /var/lib/gel && \
    chmod 0700 /var/lib/gel/data && \
    chmod -R a+rX /opt/ledger-baseline

LABEL Author="generate-harbor-tasks"
LABEL Description="Local Gel 6 server with a bare Account schema, one applied migration, four seeded accounts and a stub async ledger API in /home/user/ledger."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep gnupg
#     procps psmisc lsb-release (python3-pytest intentionally omitted; pytest comes from pip)
#   - pip3 packages: pytest, pytest-json-ctrf, gel==3.1.0 (async Python client)
#   - gel-6 apt package: local `gel-server-6` binary plus the `gel` CLI
#   - /usr/local/bin/gel-start.sh, /usr/local/bin/gel-stop.sh: idempotent local server control
#     (defensive server-binary resolution, explicit --runstate-dir, runs as the `gel` user)
#   - /var/lib/gel/data: bootstrapped instance with the baseline migration applied and the
#     accounts ACC-1001 (1000.00), ACC-1002 (500.00), ACC-1003 (250.50), ACC-1004 (0.00)
#   - /home/user/ledger: gel.toml, dbschema/default.gel (bare Account type),
#     dbschema/migrations/<hash>.edgeql (applied) and ledger_api.py (NotImplementedError stub)
#   - /opt/ledger-baseline/migrations: pristine copy of the baseline migration file

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
