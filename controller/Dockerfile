# Borrow the Docker command-line tools (docker + docker compose) from the
# official image. They are single static files, so they run fine here.
FROM docker:29-cli AS dockercli

FROM python:3.12-slim

COPY --from=dockercli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=dockercli /usr/local/libexec/docker/cli-plugins/docker-compose /usr/local/libexec/docker/cli-plugins/docker-compose

WORKDIR /app

COPY controller/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Shared with the other RootLens parts: what app to watch (services.yaml).
COPY common/topology.py services.yaml ./
COPY controller/policy.py controller/safety.py controller/notify.py controller/executor.py controller/controller.py ./

CMD ["python", "-u", "controller.py"]