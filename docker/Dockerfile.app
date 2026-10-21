# Core app — no ROS. Runs on any Docker host (Windows/macOS/Linux). Runs as a non-root user.
FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --create-home --uid 1000 adviewer
USER adviewer
WORKDIR /work
EXPOSE 8765
ENTRYPOINT ["adviewer"]
# Inside the container the server has to listen on all interfaces; docker-compose.yml
# publishes the port on the host's loopback only and the Host check stays on.
CMD ["serve", "graph.json", "--host", "0.0.0.0", "--no-auth", "--allowed-host", "localhost", "--allowed-host", "127.0.0.1"]
