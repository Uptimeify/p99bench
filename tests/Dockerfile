# Container for stage tests. Proves the scripts run and emit well-formed
# fragments. It is NOT a measurement: numbers from a container on a laptop
# describe nothing. Same reasoning as the CI smoke job.
FROM debian:13

RUN apt-get update && apt-get install -y --no-install-recommends \
      fio sysbench rt-tests redis-tools jq bc sysstat openssl \
      numactl stress-ng dmidecode procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /p99bench
