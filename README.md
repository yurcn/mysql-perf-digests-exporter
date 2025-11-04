# mysql-perf-digest-exporter

Exporter that queries MySQL Performance Schema digest statistics, ships statements to Loki, and exposes Prometheus metrics for per-digest counters. Supports multi-host MySQL configurations and Prometheus ServiceMonitor.

## Features

* Queries `performance_schema.events_statements_summary_by_digest`
* Sends normalized SQL statements to Loki with dynamic tags
* Exposes Prometheus metrics (`Counter` per digest, `digest_up` gauge)
* Supports multiple MySQL instances (multi-host)
* `/probe?instance=&target=` endpoint for on-demand scrapes
* Lightweight & async (Python 3.14)
* Production-ready Docker & Helm chart

---

## Running with Docker

```bash
docker run --rm -p 3162:3162 \
  -v $PWD/perf_digest2loki-config.yml:/app/perf_digest2loki-config.yml:ro \
  ghcr.io/yurcn/mysql-perf-digests-exporter:latest
```

Healthcheck: `GET /metrics`
Manual probe: `GET /probe?instance=<name>`

### Example Config (multi-host)

```yaml
name: perf-digest
period: 120
listen_address: 0.0.0.0
listen_port: 3162
loki:
  url: http://loki:3100/loki/api/v1/push
mysql:
  query: |
    SELECT SCHEMA_NAME, DIGEST, info AS info, COUNT_STAR AS COUNT_STAR
    FROM performance_schema.events_statements_summary_by_digest
    ORDER BY COUNT_STAR DESC LIMIT 100;
  log_column: info
  extra_tags: [SCHEMA_NAME, DIGEST, COUNT_STAR]
  instances:
    - name: instance
      host: mysql-1
      port: 3306
      user: logger
      pass: password
    - name: another
      host: mysql-2
      port: 3306
      user: logger
      pass: password
```

---

## Running with Helm

```bash
helm install perf-digests oci://ghcr.io/yurcn/helm/mysql-perf-digests-exporter \
  --set serviceMonitor.enabled=true
```

### Override MySQL instances

```bash
helm upgrade --install perf-digests oci://ghcr.io/yurcn/helm/mysql-perf-digests-exporter \
  --set config.mysql.instances[0].name=instance \
  --set config.mysql.instances[0].host=mysql-1 \
  --set config.mysql.instances[0].user=logger \
  --set config.mysql.instances[0].pass=password
```

### Use Secret for credentials

```yaml
config:
  mysql:
    secretRefs:
      - instanceName: instance
        name: perf-digests-credentials-instance
```

Secret example:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: perf-digest-credentials-instance
stringData:
  user: logger
  pass: password
```

---

## ServiceMonitor

Enable if Prometheus Operator is present:

```bash
helm upgrade --install perf-digest charts/perf-digest \
  --set serviceMonitor.enabled=true
```

Metrics are exposed at `/metrics`.

---

## License

MIT
