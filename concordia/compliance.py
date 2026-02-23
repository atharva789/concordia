from dataclasses import dataclass, field
from typing import List


@dataclass
class ComplianceReport:
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def evaluate_create_party_config(
    compliance_mode: str,
    attest_commercial_use_rights: bool,
    allow_remote_input: bool,
    claude_command: str,
) -> ComplianceReport:
    """Validate host startup policy for higher-confidence compliance defaults.

    This is a product safety gate, not legal advice.
    """

    mode = (compliance_mode or "strict").strip().lower()
    cmd = (claude_command or "").strip().lower()
    using_claude_cli = cmd.startswith("claude") or " claude" in cmd

    report = ComplianceReport()

    if mode not in {"strict", "warn", "off"}:
        report.errors.append(f"Invalid compliance mode: {compliance_mode}")
        return report

    if mode == "off":
        report.warnings.append("Compliance mode is OFF. You are responsible for policy/legal compliance.")
        return report

    if mode == "warn":
        report.warnings.append("Compliance mode WARN: startup allowed with warnings only.")
        if using_claude_cli:
            report.warnings.append(
                "This session uses Claude CLI credentials. Confirm this usage is permitted for your account/plan."
            )
        if allow_remote_input:
            report.warnings.append(
                "Remote participants can send input to the host Claude session. This may increase account-sharing risk."
            )
        if not attest_commercial_use_rights:
            report.warnings.append(
                "No commercial-rights attestation provided. Use --attest-commercial-use-rights for stronger controls."
            )
        return report

    # strict mode
    if not attest_commercial_use_rights:
        report.errors.append(
            "Strict mode requires --attest-commercial-use-rights before starting a multi-user session."
        )

    if using_claude_cli and not attest_commercial_use_rights:
        report.errors.append(
            "Strict mode blocks Claude CLI multi-user startup without explicit attestation."
        )

    if allow_remote_input and not attest_commercial_use_rights:
        report.errors.append(
            "Strict mode blocks --allow-remote-input without commercial-rights attestation."
        )

    if not allow_remote_input:
        report.warnings.append(
            "Remote input is disabled; non-host participants are view-only in strict mode unless enabled explicitly."
        )

    return report
