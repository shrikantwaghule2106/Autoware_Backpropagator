# Live probe (Online mode, Phase 4, D14, D32)

C++ (`rclcpp`) package `adviewer_probe` in `cpp/`, built with colcon on a ROS 2 image
(`ros:jazzy` by default, `Dockerfile` in this folder). It is the only code in this repository
that uses ROS; the Python core never imports it, so the core also runs on Windows and macOS
without ROS 2.

```bash
adviewer probe build                       # ros:jazzy + CycloneDDS + the probe → adviewer-probe:dev
adviewer probe run                         # Autoware on the host network, default DDS settings
adviewer probe run --like autoware         # Autoware in a container: join it (see below)
adviewer up --online                       # Online mode and the Live dashboard in the UI
adviewer compare lidar_only.json lidar_cameras.json   # FR-10: which rates drop under load
```

## How `adviewer probe run` joins your setup

DDS discovery works only when the probe shares Autoware's network, DDS implementation, domain
and DDS settings; a topic is measured only when its message package is installed where the
probe runs. `--like CONTAINER` reads `docker inspect CONTAINER` and runs the probe with:

- the container's **image** (all its message packages; the probe's own files are mounted
  from the probe image, so both must be the same ROS 2 distribution);
- its **network**: `host`, or the container's own network namespace (a compose network,
  loopback-only DDS settings and all);
- its **IPC** namespace when it is `host` or `shareable` (shared-memory transports);
- its **ROS/DDS environment**: `ROS_DOMAIN_ID`, `ROS_LOCALHOST_ONLY`,
  `ROS_AUTOMATIC_DISCOVERY_RANGE`, `ROS_STATIC_PEERS`, `RMW_*`, `CYCLONEDDS*`, `FASTRTPS*`,
  `FASTDDS*`, `ZENOH*`;
- the **mounts that hold the DDS config files** named there (read-only); a file inside the
  image is found because the probe runs in that image.

It sources `/opt/ros/$ROS_DISTRO/setup.bash`, `/opt/autoware/setup.bash` and
`/autoware/install/setup.bash` when present, plus any `--setup FILE`. Without `--like` the
probe runs in its own image on the host network (`--network` to change it) with
`--rmw`/`--domain`/`--cyclone-config`/`--env`. `--in-image IMAGE` runs it inside another image
with those options. The probe runs as your user (`--root` to change that).

It also fixes on its own what would otherwise fail on another machine or image:
- the probe image is built when it is missing or older than this adviewer;
- `--like` a container on another ROS 2 distribution builds `adviewer-probe:<distro>` from
  `ros:<distro>` and uses it (`adviewer probe build --base <image> --image …` does it by hand);
- with Fast DDS (also ROS 2's default when `RMW_IMPLEMENTATION` is unset) the probe receives
  over UDP: Fast DDS's shared memory does not cross containers or users, so topics would be
  seen but no data would arrive. A Fast DDS profile of your own is left alone;
- on SELinux hosts (Fedora, RHEL) the probe gets `label=disable` so it can read its mounts;
- on Docker Desktop (macOS, Windows), which has no host network, it asks for `--like`;
- with `--like`, a restart or a recreate of the container (`docker restart`, `docker compose up
  --force-recreate`, `down` then `up`) gives it a new network namespace: the probe stops, waits
  until the container runs again and joins it there, instead of measuring nothing.

## Autoware built on this machine (`--native`)

`adviewer probe build --native` copies this package into its own overlay workspace
(`~/.cache/adviewer/probe-native`) and builds it on top of ROS 2 and Autoware: it sources
`/opt/ros/$ROS_DISTRO/setup.bash` and the first of `~/autoware/install/setup.bash`,
`/opt/autoware/setup.bash` it finds, or the `--setup` files you give, in order. `adviewer probe
run --native` runs it in your shell's environment (your RMW, domain and DDS settings; override
with `--rmw`, `--domain`, `--cyclone-config`, `--env`), so it reads every Autoware message type.
Both leave adviewer's own virtualenv out of the environment they give ROS 2. Stopping adviewer
(Ctrl+C, or stopping the VS Code task) stops the probe.

Parameters: `window` (s, 5.0), `discovery` (s, 2.0), `output`, `include` / `exclude`
(regex on topic names; default excludes `/rosout`, `/parameter_events`), `subscribe`
(false = graph and QoS only, no data delivered to the probe), `loop`.

`probe_dump.json`:

```json
{"captured_at": "…Z", "window_s": 5.0, "rmw": "rmw_cyclonedds_cpp", "domain_id": "0",
 "subscribed": true, "nodes": ["/ns/node", …],
 "topics": [{"name": "/x", "types": ["pkg/msg/T"],
             "publishers": [{"node": "/ns/n", "type": "pkg/msg/T",
                             "qos": {"reliability": "reliable", "durability": "volatile",
                                     "history": "unknown", "depth": 0}}],
             "subscribers": […], "measured": true, "count": 50, "rate_hz": 10.0,
             "jitter_ms": 0.05, "max_gap_ms": 100.1, "bandwidth_Bps": 160.0, "mean_size_B": 16}]}
```

Known limits:
- `history`/`depth` of other processes' endpoints are not carried by DDS discovery
  (reported `unknown`/0); reliability and durability are.
- A rate is measured by subscribing, so DDS delivers the data to the probe. Run it on the
  machine (or container) you want to measure, and prefer `subscribe:=false` or a narrow
  `include` for very large topics. ROS 2 headers have no sequence number, so loss is
  inferred from rate, gaps and jitter, and a sending-vs-receiving comparison needs one
  probe per container (roadmap Phase 4).
- A topic is measured only if its message package is installed where the probe runs; other
  topics are listed with their endpoints, logged once and marked `measured: false`. With
  `--like` the probe runs in Autoware's image, so Autoware's own types are all there.
- Tested: publishers in containers on the host network (2026-10-16); a container on its own
  Docker network with a loopback-only, no-multicast CycloneDDS file, joined with `--like`
  (2026-10-17: the default probe sees nothing, `--like` measures every topic and records a
  node starting next to it); a live Autoware 1.9.0 stack (2026-10-17). Built on `ros:humble`,
  the probe measures a Humble publisher (2026-10-17).
