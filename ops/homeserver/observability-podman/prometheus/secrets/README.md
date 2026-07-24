# Prometheus scrape secret

The live activation packet may create an untracked file named exactly
`odysseus_metrics_token` in this directory with mode `0600`. Its single line
must be an Odysseus API token whose scope is exactly `observability:read`.

The token file is intentionally absent from the repository. Do not put a real
token, example token, password, internal app token, or exported environment
value in this directory. Do not create the file before the explicit
`GRO-LIVE-ACTIVATION` decision.

