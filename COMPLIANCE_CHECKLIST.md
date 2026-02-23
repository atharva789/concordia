# Concordia Compliance Checklist

This is an operational compliance checklist for Concordia maintainers and hosts.
It is **not legal advice**.

## Sources to Monitor

- Anthropic Consumer Terms: https://www.anthropic.com/legal/consumer-terms
- Anthropic Commercial Terms: https://www.anthropic.com/legal/commercial-terms
- Claude Code legal/compliance docs: https://code.claude.com/docs/en/legal-and-compliance
- Anthropic Usage Policy: https://www.anthropic.com/legal/aup
- Anthropic API safeguards tools: https://support.anthropic.com/en/articles/9199617-api-safeguards-tools

## Current Product Risks (Shared Claude Session)

- Shared-session behavior may be interpreted as account sharing when multiple people drive one host session.
- Consumer-plan/OAuth credential usage in third-party orchestration may be restricted.
- Abuse/policy violations from participants can impact host account standing.

## Product Safety Defaults (Implemented)

- `--compliance-mode strict` is default.
- Strict mode requires `--attest-commercial-use-rights` before party startup.
- Remote input is disabled by default (non-host participants are view-only) unless host explicitly passes `--allow-remote-input`.
- Optional client verification gate is available: `--require-client-claude-check` (fresh local probe required for non-host join).
- Optional estimate-only usage attribution report is available via `--estimate-token-usage`.
- Input stream safeguards:
  - max input chunk size
  - per-user input rate limiting
- Append-only audit log enabled by default (`concordia-audit.log`) with metadata and chunk hash.

## Required Host Actions

Before running a multi-user session, host must verify:

1. Their account/plan permits this usage model.
2. Their intended use follows applicable Anthropic usage policy.
3. They accept responsibility for participant prompts routed through host session.

## Launch/Release Gate

Do not mark a public release as "compliant" unless all are true:

- Strict mode remains default.
- Startup attestation gate remains enabled.
- Non-host input defaults to disabled.
- Audit logging and input safeguards are active.
- README and CLI help clearly describe compliance flags and risks.

## Operational Controls (Recommended)

- Keep abuse response process for hosts (pause/terminate session quickly).
- Keep data retention policy for audit logs.
- Add legal counsel review before paid/commercial rollout.
- For hosted relay deployment, add auth/rate limits/monitoring at relay level.

## Further Legal Hardening (Recommended)

- Show a per-client terms notice at join and record explicit acceptance in the audit log.
- Keep view-only as default for strict mode; require explicit host opt-in for remote input each session.
- Add participant identity capture (email/org/user ID) where allowed, and log who controlled input windows.
- Provide a host-visible policy banner that states this is a shared-control session and may be restricted by provider terms.
- Publish a short admin policy for hosts: acceptable use, incident response, retention period, and how to export/delete logs.
- For exact per-user billing/accounting, migrate from shared PTY attribution to API-native request usage metrics.

## CLI Compliance Matrix

- **strict**: hard enforcement, blocks startup without attestation.
- **warn**: startup allowed with warnings.
- **off**: no startup enforcement; host accepts full responsibility.

## Notes

Concordia aims to stay close to its original collaborative UX while minimizing obvious compliance risk.
For strictest posture, use view-only participants unless legal/commercial approval for remote co-control is explicit.
