# Grafana activation secret

The final live activation packet may create an untracked file named exactly
`grafana_admin_password` in this directory with mode `0600`. The value must be
generated during the gated activation and must never be copied into Git,
dashboard JSON, provisioning YAML, logs, or chat output.

The admin user and Prometheus URL are supplied separately through the gated
systemd environment file. No user, URL, password, token, or contact point is
fixed in these repository assets.

