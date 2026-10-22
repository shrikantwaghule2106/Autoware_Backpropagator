# Live probe (Online mode, D14, D32) — C++ rclcpp on ROS 2 (Jazzy by default).
# Build context = this folder (shipped inside the Python package): `adviewer probe build`.
# Another ROS 2 distribution: `adviewer probe build --base <a ROS 2 image with colcon and a compiler>`.
ARG BASE=ros:jazzy
FROM ${BASE}
USER root
# `adviewer probe run` checks this label: an older probe image is rebuilt instead of failing at start
LABEL adviewer.probe="3"
SHELL ["/bin/bash", "-c"]
RUN test -n "$ROS_DISTRO" || { echo "the base image must be a ROS 2 image (ROS_DISTRO is not set)"; exit 1; }
# Autoware runs on CycloneDDS and `adviewer probe run` selects it; ros:jazzy ships only Fast DDS
# (downloads retried: a brief network or package-server hiccup should not fail the build)
RUN dpkg -s "ros-$ROS_DISTRO-rmw-cyclonedds-cpp" >/dev/null 2>&1 || { apt-get -o Acquire::Retries=3 update \
 && apt-get -o Acquire::Retries=3 install -y --no-install-recommends "ros-$ROS_DISTRO-rmw-cyclonedds-cpp" \
 && rm -rf /var/lib/apt/lists/*; }
WORKDIR /ws
COPY cpp /ws/src/adviewer_probe
RUN . "/opt/ros/$ROS_DISTRO/setup.sh" \
 && colcon build --packages-select adviewer_probe --cmake-args -DCMAKE_BUILD_TYPE=Release \
 && rm -rf build log
WORKDIR /work
# Examples:  docker run --rm --net=host -v $PWD:/work adviewer-probe:dev
#            … adviewer-probe:dev --ros-args -p window:=10.0 -p loop:=true
# exec the binary (not `ros2 run`, which does not pass SIGINT/SIGTERM on): Ctrl+C and `docker stop` stop it
ENTRYPOINT ["/bin/bash", "-c", "source /opt/ros/$ROS_DISTRO/setup.bash && source /ws/install/setup.bash && exec \"$(ros2 pkg prefix adviewer_probe)/lib/adviewer_probe/probe\" \"$@\"", "--"]
CMD ["--ros-args", "-p", "output:=/work/probe_dump.json"]
