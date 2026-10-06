# Security

If you find a vulnerability in this integration, or in the Phare API it talks to, please
email security@pharelabs.com rather than opening a public issue. We will acknowledge within
two working days.

What this integration stores: your personal access token and, when pushed alerts are
enabled, a webhook signing secret. Both live in Home Assistant's config entry storage
and are redacted from diagnostics downloads.
