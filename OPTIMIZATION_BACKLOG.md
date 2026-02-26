# Optimization Backlog

1. API config-driven provider loading
- Goal: expose provider module selection in API-side configuration as a control plane capability.
- Why:
  - rollout/rollback without code changes in core
  - environment-specific provider matrix (dev/staging/prod)
  - easier canary for new site providers
- Notes:
  - keep allowlist for importable modules
  - add validation and startup diagnostics for loaded providers
