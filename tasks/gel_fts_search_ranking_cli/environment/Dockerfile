FROM ubuntu:24.04

ARG POCHI_VERSION

RUN apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        curl wget ca-certificates gnupg git python3 python3-pip bash ripgrep procps jq && \
    ln -s /usr/bin/python3 /usr/bin/python && \
    curl -fsSL https://getpochi.com/install.sh | bash -s ${POCHI_VERSION} && \
    ln -s /root/.pochi/bin/pochi /usr/local/bin/pochi && \
    pip3 install --break-system-packages pytest pytest-json-ctrf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Gel 7 (bundles the gel-server-7 binary and the gel CLI).
RUN mkdir -p /usr/local/share/keyrings && \
    curl --proto '=https' --tlsv1.2 -sSf \
        -o /usr/local/share/keyrings/gel-keyring.gpg \
        https://packages.geldata.com/keys/gel-keyring.gpg && \
    echo 'deb [signed-by=/usr/local/share/keyrings/gel-keyring.gpg] https://packages.geldata.com/apt noble main' \
        > /etc/apt/sources.list.d/gel.list && \
    apt-get update -y && \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends gel-7 && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Connection settings for the bundled local instance (no secrets: trust auth, loopback only).
ENV GEL_DSN="gel://admin@127.0.0.1:5656/main"
ENV GEL_CLIENT_TLS_SECURITY="insecure"

# Idempotent starter for the bundled local instance. It resolves the server binary
# defensively and always passes an explicit, pre-created runstate directory.
RUN cat > /usr/local/bin/gel-start <<'GELSTART_EOF'
#!/usr/bin/env bash
DATA_DIR=/var/lib/gel/data
RUN_DIR=/var/lib/gel/run
LOG_FILE=/var/log/gel-server.log
PORT=5656

if gel query --wait-until-available=1s "select 1" >/dev/null 2>&1; then
    exit 0
fi

SERVER_BIN=""
for candidate in gel-server gel-server-7 gel-server-6; do
    if command -v "$candidate" >/dev/null 2>&1; then
        SERVER_BIN="$(command -v "$candidate")"
        break
    fi
done
if [ -z "$SERVER_BIN" ]; then
    SERVER_BIN="$(ls /usr/bin/gel-server* 2>/dev/null | head -n 1)"
fi
if [ -z "$SERVER_BIN" ]; then
    echo "gel-start: no gel-server binary found (tried gel-server, gel-server-7, gel-server-6, /usr/bin/gel-server*)" >&2
    exit 1
fi

mkdir -p "$DATA_DIR" "$RUN_DIR"
touch "$LOG_FILE"
if [ "$(id -u)" = "0" ]; then
    chown -R gel:gel /var/lib/gel "$LOG_FILE"
fi

SERVER_ARGS="--data-dir=$DATA_DIR --runstate-dir=$RUN_DIR --port=$PORT --bind-address=127.0.0.1"
SERVER_ARGS="$SERVER_ARGS --security=insecure_dev_mode --default-auth-method=Trust"
SERVER_ARGS="$SERVER_ARGS --tls-cert-mode=generate_self_signed"

if [ "$(id -u)" = "0" ]; then
    setsid su gel -s /bin/bash -c "nohup $SERVER_BIN $SERVER_ARGS >> $LOG_FILE 2>&1 &"
else
    setsid nohup $SERVER_BIN $SERVER_ARGS >> "$LOG_FILE" 2>&1 &
fi

if gel query --wait-until-available=180s "select 1" >/dev/null 2>&1; then
    exit 0
fi

echo "gel-start: the local Gel instance did not become available; last log lines:" >&2
tail -n 40 "$LOG_FILE" >&2
exit 1
GELSTART_EOF

RUN chmod 0755 /usr/local/bin/gel-start && \
    mkdir -p /var/lib/gel/data /var/lib/gel/run && \
    touch /var/log/gel-server.log && \
    chown -R gel:gel /var/lib/gel /var/log/gel-server.log

# Bootstrap the data directory once at build time, then shut the server down again.
RUN gel-start && \
    gel query -F json "select sys::get_version_as_str()" && \
    gel query -F json "select count(schema::Migration)" && \
    pkill -TERM -f gel-server && \
    sleep 10 && \
    echo "gel data directory bootstrapped"

RUN mkdir -p /home/user && chmod 0755 /home/user

RUN mkdir -p /home/user/kb-search/dbschema /home/user/kb-search/scripts \
        /home/user/kb-search/data /opt/kb-fixtures

RUN cat > /home/user/kb-search/gel.toml <<'GELTOML_EOF'
[instance]
server-version = "7.1"
GELTOML_EOF

RUN cat > /home/user/kb-search/dbschema/default.gel <<'SCHEMA_EOF'
module default {
}
SCHEMA_EOF

RUN cat > /home/user/kb-search/data/corpus.json <<'CORPUS_EOF'
[
  {
    "slug": "alpine-trail-safety",
    "title": "Alpine Trail Safety",
    "summary": "Checklist for high routes above the tree line.",
    "body": "Plan every climb with a map and check the forecast before you start.",
    "section": "safety",
    "published": true
  },
  {
    "slug": "bear-canister-use",
    "title": "Bear Canister Use",
    "summary": "How to store scented items overnight.",
    "body": "Set the canister two hundred steps away from camp and close both latches.",
    "section": "safety",
    "published": true
  },
  {
    "slug": "beginner-kayak-basics",
    "title": "Kayak Basics",
    "summary": "First lesson for flat water paddlers.",
    "body": "Sit upright, grip the paddle lightly, and steer with your torso.",
    "section": "water",
    "published": true
  },
  {
    "slug": "campfire-rules",
    "title": "Campfire Rules",
    "summary": "Local fire regulations in plain language.",
    "body": "Use existing rings and drown the embers before you leave.",
    "section": "safety",
    "published": true
  },
  {
    "slug": "coastal-kayak-tips",
    "title": "Kayak Tips",
    "summary": "Short notes for salt water outings.",
    "body": "Watch the tide charts and keep a whistle within reach.",
    "section": "water",
    "published": true
  },
  {
    "slug": "desert-trail-water",
    "title": "Desert Trail Water Planning",
    "summary": "How much to carry on dry routes.",
    "body": "Carry four liters and cache extra near the road crossing.",
    "section": "hiking",
    "published": true
  },
  {
    "slug": "dog-friendly-outings",
    "title": "Dog Friendly Outings",
    "summary": "Where pets are welcome.",
    "body": "Keep your dog leashed on any shared trail.",
    "section": "community",
    "published": true
  },
  {
    "slug": "first-aid-kit",
    "title": "First Aid Kit Checklist",
    "summary": "Contents for a two day outing.",
    "body": "Include blister pads, tape, gloves, and small scissors.",
    "section": "safety",
    "published": true
  },
  {
    "slug": "forest-trail-markers",
    "title": "Forest Trail Markers",
    "summary": "Reading blazes in dense woods.",
    "body": "Blazes are painted at eye level on the uphill side of each tree.",
    "section": "hiking",
    "published": true
  },
  {
    "slug": "harbor-fog-signals",
    "title": "Harbor Fog Signals",
    "summary": "Sound rules when visibility drops.",
    "body": "Sound one long blast every two minutes and slow down.",
    "section": "water",
    "published": true
  },
  {
    "slug": "knot-practice",
    "title": "Knot Practice",
    "summary": "Three knots that cover most camp needs.",
    "body": "The bowline, taut line hitch, and figure eight are enough.",
    "section": "skills",
    "published": true
  },
  {
    "slug": "layering-basics",
    "title": "Layering Basics",
    "summary": "Choosing fabrics for changing weather.",
    "body": "Wool next to skin, fleece in the middle, shell on the outside.",
    "section": "gear",
    "published": true
  },
  {
    "slug": "map-and-compass",
    "title": "Map And Compass Drills",
    "summary": "Practice routines for open ground.",
    "body": "Take a bearing in a field before you rely on it in the woods.",
    "section": "skills",
    "published": true
  },
  {
    "slug": "mountain-bike-setup",
    "title": "Mountain Bike Setup",
    "summary": "Quick adjustments before a ride.",
    "body": "Lower the saddle before a steep trail descent.",
    "section": "cycling",
    "published": true
  },
  {
    "slug": "night-navigation",
    "title": "Night Navigation",
    "summary": "Moving safely after sunset.",
    "body": "Learn the harbor lights and keep a spare headlamp in your pack.",
    "section": "skills",
    "published": true
  },
  {
    "slug": "northern-aurora-watch",
    "title": "Aurora Watch Nights",
    "summary": "Best hours for the northern sky show.",
    "body": "Face north, let your eyes adjust, and keep the camera on a tripod.",
    "section": "community",
    "published": true
  },
  {
    "slug": "packraft-storage",
    "title": "Packraft Storage",
    "summary": "Keeping a small boat healthy off season.",
    "body": "Roll the boat loosely and keep it out of direct sunlight.",
    "section": "gear",
    "published": true
  },
  {
    "slug": "photo-golden-hour",
    "title": "Photo Golden Hour",
    "summary": "Light planning for landscape shots.",
    "body": "Shoot within an hour of sunrise for soft light and long shadows.",
    "section": "skills",
    "published": true
  },
  {
    "slug": "repair-tape-tricks",
    "title": "Repair Tape Tricks",
    "summary": "Field fixes with one roll.",
    "body": "Wrap tape around a pole section so it is always with you.",
    "section": "gear",
    "published": true
  },
  {
    "slug": "ridge-camera-settings",
    "title": "Ridge Camera Settings",
    "summary": "Exposure notes for aurora chasers.",
    "body": "Use a wide lens, open the aperture, and bracket the exposure.",
    "section": "skills",
    "published": true
  },
  {
    "slug": "river-paddle-routes",
    "title": "River Paddle Routes",
    "summary": "Three slow water loops near town.",
    "body": "A kayak works well where portage sections are short.",
    "section": "water",
    "published": true
  },
  {
    "slug": "secret-kayak-clinic",
    "title": "Kayak Clinic Announcement",
    "summary": "Internal draft for an upcoming clinic.",
    "body": "Dates for the kayak clinic are unconfirmed and not ready to share.",
    "section": "community",
    "published": false
  },
  {
    "slug": "snowshoe-rentals",
    "title": "Snowshoe Rentals",
    "summary": "Reservation windows and prices.",
    "body": "Reserve gear a week ahead during the holiday season.",
    "section": "gear",
    "published": true
  },
  {
    "slug": "stove-fuel-math",
    "title": "Stove Fuel Math",
    "summary": "Estimating gas for a group.",
    "body": "Budget ten grams of gas per person for each hot meal.",
    "section": "gear",
    "published": true
  },
  {
    "slug": "sunrise-summit-plan",
    "title": "Sunrise Summit Plan",
    "summary": "Timing an early start.",
    "body": "Leave two hours before dawn to reach the ridge in daylight and you may catch an aurora glow in autumn.",
    "section": "hiking",
    "published": true
  },
  {
    "slug": "tent-pitching",
    "title": "Tent Pitching Guide",
    "summary": "Setting up on uneven ground.",
    "body": "Stake the windward corner first and keep the door downwind.",
    "section": "gear",
    "published": true
  },
  {
    "slug": "trail-food-planning",
    "title": "Trail Food Planning",
    "summary": "Meal math for long days.",
    "body": "Pack dense snacks and split them into daily bags.",
    "section": "food",
    "published": true
  },
  {
    "slug": "water-filter-care",
    "title": "Water Filter Care",
    "summary": "Cleaning routine after each trip.",
    "body": "Backflush the cartridge and store it dry with the cap off.",
    "section": "gear",
    "published": true
  },
  {
    "slug": "wildlife-etiquette",
    "title": "Wildlife Etiquette",
    "summary": "Keeping distance from animals.",
    "body": "Give animals room and never feed them along the route.",
    "section": "community",
    "published": true
  },
  {
    "slug": "winter-trail-traction",
    "title": "Winter Trail Traction",
    "summary": "Choosing spikes for icy days.",
    "body": "Microspikes bite well on packed snow and steep switchbacks.",
    "section": "winter",
    "published": true
  },
  {
    "slug": "zephyr-jacket-review",
    "title": "Zephyr Jacket Review",
    "summary": "Draft review of a windproof shell.",
    "body": "Held back until the zephyr sample returns from testing.",
    "section": "gear",
    "published": false
  }
]
CORPUS_EOF

RUN sha256sum /home/user/kb-search/data/corpus.json | awk '{print $1}' > /opt/kb-fixtures/corpus.sha256 && \
    chmod 0644 /opt/kb-fixtures/corpus.sha256 && \
    cat /opt/kb-fixtures/corpus.sha256

LABEL Author="generate-harbor-tasks"
LABEL Description="Gel 7.1 (server + CLI) with a bootstrapped local instance and an unmodelled JSON article corpus in /home/user/kb-search for a weighted full-text search task."

CMD ["/bin/bash", "-c", "trap : TERM INT; sleep infinity & wait"]

# Container contents:
#   - always required apt packages: curl wget ca-certificates git python3 python3-pip bash ripgrep
#     (python3-pytest is intentionally NOT installed: the distro pytest breaks pytest-json-ctrf)
#   - extra apt packages: gnupg (repo key), procps (pkill), jq
#   - pip3 packages: pytest, pytest-json-ctrf
#   - Gel 7.1 from packages.geldata.com: gel CLI + gel-server-7 binary
#   - /usr/local/bin/gel-start: idempotent starter for the local instance
#     (explicit --runstate-dir=/var/lib/gel/run, data dir /var/lib/gel/data, loopback only)
#   - /var/lib/gel/data: data directory bootstrapped at build time (empty 'main' branch)
#   - ENV GEL_DSN / GEL_CLIENT_TLS_SECURITY: connection settings for the local instance
#   - /home/user/kb-search: Gel project (gel.toml, empty dbschema/default.gel, empty scripts/)
#   - /home/user/kb-search/data/corpus.json: 31 article records to be modelled and loaded
#   - /opt/kb-fixtures/corpus.sha256: checksum of the pristine corpus file

COPY test_initial_state.py /bootstrap/test_initial_state.py

# Setup entrypoint script
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
