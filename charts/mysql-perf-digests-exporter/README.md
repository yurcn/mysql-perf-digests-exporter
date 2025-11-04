# mysql-perf-digest-exporter Helm chart

## Multi-host config
See `values.yaml -> config.mysql.instances`. You may inline credentials or reference per-instance Secrets via `config.mysql.secretRefs`.

## ServiceMonitor
Enable via `serviceMonitor.enabled=true`. The app exposes Prometheus metrics on `/metrics` and supports on-demand `/probe?instance=&target=`.
