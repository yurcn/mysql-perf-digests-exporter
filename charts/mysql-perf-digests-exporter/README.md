# mysql-perf-digest-exporter Helm chart

## Multi-host config
See `values.yaml -> config.mysql.instances`. You may inline credentials or reference per-instance Secrets via `config.mysql.secretRefs`.

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

## ServiceMonitor
Enable via `serviceMonitor.enabled=true`. The app exposes Prometheus metrics on `/metrics` and supports on-demand `/probe?instance=&target=`.
