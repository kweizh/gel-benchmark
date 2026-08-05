# Task environment for gel_multitenant_access_policies_py.
#
# The official Gel 7.1 image is used as the base so that the database server and
# the `gel` CLI are both available locally: the whole task (server, CLI, tests)
# runs inside this single container, with no external services at all.
FROM geldata/gel:7.1

ARG POCHI_VERSION

# --- Gel server configuration (local, single container) ---------------------
# The data directory deliberately sits outside the image's declared VOLUME so
# that all database state stays inside the container filesystem.
ENV GEL_SERVER_DATADIR=/var/lib/gel-data
ENV GEL_SERVER_SECURITY=insecure_dev_mode
ENV GEL_SERVER_TLS_CERT_MODE=generate_self_signed
# The listen address and the port keep their defaults (all interfaces, 5656);
# GEL_SERVER_PORT is deliberately left unset because the entrypoint refuses to
# start when both it and a client-side port variable are present.
ENV GEL_SERVER_COMPILER_POOL_MODE=on_demand
ENV GEL_SERVER_COMPILER_POOL_SIZE=2
ENV GEL_DOCKER_APPLY_MIGRATIONS=never

# --- Client connection settings used by the CLI and the graders -------------
ENV GEL_DSN=gel://admin@127.0.0.1:5656
ENV GEL_CLIENT_TLS_SECURITY=insecure

# The Gel apt source is dropped first: the server and the CLI are already part of
# the base image, so the build only needs the plain Debian archives.
RUN rm -f /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep && \
    ln -sf /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest-json-ctrf gel==3.1.0 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN mkdir -p /home/user && chmod 0755 /home/user

# --- helper that starts the local Gel server and waits until it is ready ----
RUN cat >/usr/local/bin/gel-up <<'EOF'
#!/usr/bin/env bash
# Start the local Gel server if it is not running yet and wait until it is
# ready to answer queries.  Safe to run any number of times.
PORT="${GEL_SERVER_PORT:-5656}"
LOG=/var/log/gel-server.log

ready() {
  curl -fsS -m 5 "http://127.0.0.1:${PORT}/server/status/ready" >/dev/null 2>&1 && return 0
  curl -fsSk -m 5 "https://127.0.0.1:${PORT}/server/status/ready" >/dev/null 2>&1 && return 0
  return 1
}

if ! ready; then
  if ! pgrep -f "gel-server" >/dev/null 2>&1; then
    touch "$LOG"
    nohup /usr/local/bin/docker-entrypoint.sh server >>"$LOG" 2>&1 &
  fi
fi

for _ in $(seq 1 300); do
  if ready; then
    echo "gel-up: the local Gel server is ready on port ${PORT}"
    exit 0
  fi
  sleep 1
done

echo "gel-up: timed out waiting for the local Gel server" >&2
if [ -f "$LOG" ]; then
  echo "gel-up: last lines of ${LOG}:" >&2
  tail -n 40 "$LOG" >&2
fi
exit 1
EOF

RUN chmod 0755 /usr/local/bin/gel-up && \
    mkdir -p /var/lib/gel-data && \
    chown gel:gel /var/lib/gel-data && \
    chmod 700 /var/lib/gel-data && \
    touch /var/log/gel-server.log && \
    chmod 0666 /var/log/gel-server.log

# Bootstrap the instance (cluster + `main` branch + self-signed certificate) at
# build time so that starting the server later is fast.  If bootstrapping is not
# possible during the build, the data directory is left empty and the very first
# `gel-up` run performs the bootstrap instead.
RUN GEL_SERVER_BOOTSTRAP_ONLY=1 /usr/local/bin/docker-entrypoint.sh server || \
    echo "gel bootstrap deferred to the first server start"

# --- project skeleton -------------------------------------------------------
RUN mkdir -p /home/user/tenantdesk/dbschema /home/user/tenantdesk/seed

RUN cat >/home/user/tenantdesk/dbschema/default.gel <<'EOF'
module default {
  scalar type ActorRole extending enum<admin, agent, readonly>;
  scalar type TicketStatus extending enum<open, pending, closed>;

  global current_actor_email: str;

  type Tenant {
    required slug: str {
      constraint exclusive;
    }
    required name: str;
  }

  type Actor {
    required email: str {
      constraint exclusive;
    }
    required tenant: Tenant;
    required role: ActorRole;
  }

  type Ticket {
    required ref: str;
    required subject: str;
    required status: TicketStatus {
      default := TicketStatus.open;
    }
    required tenant: Tenant;

    index on (.ref);
  }
}
EOF

RUN cat >/home/user/tenantdesk/app.py <<'EOF'
#!/usr/bin/env python3
"""tenantdesk support-desk CLI (legacy version).

Tenant scoping happens here in Python: the caller tells us which tenant it wants
to work with, we read every ticket in the database and drop the ones that do not
match.  Anything that talks to the database without going through this file sees
all of the data.
"""

import argparse
import json
import sys

import gel

TICKET_SHAPE = """
    {
        ref,
        subject,
        status_str := <str>.status,
        tenant_slug := .tenant.slug
    }
"""


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="app.py")
    subcommands = parser.add_subparsers(dest="command", required=True)

    listing = subcommands.add_parser("list-tickets")
    listing.add_argument("--actor", required=True)

    creating = subcommands.add_parser("create-ticket")
    creating.add_argument("--actor", required=True)
    creating.add_argument("--tenant", required=True)
    creating.add_argument("--ref", required=True)
    creating.add_argument("--subject", required=True)

    return parser.parse_args(argv)


def as_dict(row):
    return {
        "ref": row.ref,
        "subject": row.subject,
        "status": row.status_str,
        "tenant": row.tenant_slug,
    }


def actor_tenant(client, actor):
    return client.query_single(
        """
        select assert_single((
            select Actor filter .email = <str>$email
        )).tenant.slug
        """,
        email=actor,
    )


def list_tickets(client, actor):
    wanted = actor_tenant(client, actor)
    rows = client.query("select Ticket " + TICKET_SHAPE)
    return [as_dict(row) for row in rows if row.tenant_slug == wanted]


def create_ticket(client, actor, tenant, ref, subject):
    row = client.query_single(
        """
        select (
            insert Ticket {
                ref := <str>$ref,
                subject := <str>$subject,
                tenant := assert_exists(assert_single((
                    select Tenant filter .slug = <str>$tenant
                )))
            }
        ) """
        + TICKET_SHAPE,
        ref=ref,
        subject=subject,
        tenant=tenant,
    )
    return as_dict(row)


def main(argv):
    args = parse_args(argv)
    client = gel.create_client()
    try:
        if args.command == "list-tickets":
            print(json.dumps(list_tickets(client, args.actor)))
        else:
            print(
                json.dumps(
                    create_ticket(
                        client, args.actor, args.tenant, args.ref, args.subject
                    )
                )
            )
    finally:
        client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
EOF

RUN python3 - <<'EOF'
import json

TENANTS = [
    {"slug": "acme", "name": "Acme Industrial"},
    {"slug": "globex", "name": "Globex Systems"},
    {"slug": "initech", "name": "Initech Holdings"},
]

ACTORS = [
    {"email": "ava@acme.example", "tenant": "acme", "role": "admin"},
    {"email": "ben@acme.example", "tenant": "acme", "role": "agent"},
    {"email": "cleo@acme.example", "tenant": "acme", "role": "readonly"},
    {"email": "dan@globex.example", "tenant": "globex", "role": "admin"},
    {"email": "eve@globex.example", "tenant": "globex", "role": "agent"},
    {"email": "fay@initech.example", "tenant": "initech", "role": "admin"},
    {"email": "gus@initech.example", "tenant": "initech", "role": "readonly"},
]

STATUSES = ["open", "pending", "closed"]
SIZES = [("acme", 300), ("globex", 200), ("initech", 100)]

tickets = []
for slug, size in SIZES:
    for number in range(1, size + 1):
        tickets.append(
            {
                "ref": "%s-%04d" % (slug.upper(), number),
                "subject": "%s intake %04d" % (slug, number),
                "status": STATUSES[(number - 1) % 3],
                "tenant": slug,
            }
        )

with open("/home/user/tenantdesk/seed/dataset.json", "w", encoding="utf-8") as handle:
    json.dump(
        {"tenants": TENANTS, "actors": ACTORS, "tickets": tickets},
        handle,
        indent=2,
    )
    handle.write("\n")
EOF

RUN chmod 0644 /home/user/tenantdesk/dbschema/default.gel \
        /home/user/tenantdesk/seed/dataset.json && \
    chmod 0755 /home/user/tenantdesk/app.py

WORKDIR /home/user

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 7.1 server plus a Python multi-tenant support-desk project that must be secured with database-enforced tenant isolation."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - base image geldata/gel:7.1 (Gel 7.1 server + `gel` CLI, Debian bookworm)
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip python3-pytest bash ripgrep
#   - always required pip3 packages: pytest-json-ctrf
#   - pip3 package gel==3.1.0: Python client used by the project and by the graders
#   - /usr/local/bin/gel-up: starts the local Gel server and waits until it is ready
#   - /var/lib/gel-data: Gel data directory, bootstrapped during the image build
#   - /home/user/tenantdesk: project directory
#   - /home/user/tenantdesk/app.py: legacy CLI that filters tenants in Python
#   - /home/user/tenantdesk/dbschema/default.gel: baseline schema without any isolation
#   - /home/user/tenantdesk/seed/dataset.json: 3 tenants, 7 actors, 600 tickets

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
