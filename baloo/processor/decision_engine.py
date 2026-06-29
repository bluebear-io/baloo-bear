"""Decision engine for PR approval/rejection."""

from baloo.config.settings import get_settings
from baloo.fidelity.models import FidelityResult
from baloo.github.models import GeneralFinding, ReviewComment
from baloo.processor.severity_router import ReviewSeverity, count_by_severity


class DecisionEngine:
    """Determine whether to approve or request changes on a PR."""

    @staticmethod
    def make_decision(
        comments: list[ReviewComment],
        fidelity_result: FidelityResult | None = None,
        general_findings: list[GeneralFinding] | None = None,
    ) -> tuple[bool, bool]:
        """
        Determine review decision based on findings and fidelity score.

        Args:
            comments: List of review comments
            fidelity_result: Optional fidelity analysis result
            general_findings: Optional list of general (non-inline) findings

        Returns:
            Tuple of (approve, request_changes)
        """
        settings = get_settings()

        # Count by severity using shared utility; also fold in general findings
        counts = count_by_severity(comments)
        for gf in general_findings or []:
            sev = gf.severity.value if hasattr(gf.severity, "value") else gf.severity
            counts[sev] = counts.get(sev, 0) + 1
        critical_count = counts.get(ReviewSeverity.CRITICAL.value, 0)
        high_count = counts.get(ReviewSeverity.HIGH.value, 0)

        # Request changes if there are critical or high severity issues
        if critical_count > 0 or high_count > 0:
            return (False, True)

        # If fidelity score is high, approve (even with MEDIUM issues)
        # Clean = no CRITICAL or HIGH (we already checked above)
        has_high_fidelity = (
            fidelity_result is not None
            and fidelity_result.fidelity_score >= settings.fidelity_approval_threshold
        )

        if has_high_fidelity:
            # High fidelity score - approve regardless of MEDIUM issues
            return (True, False)

        # For medium/low issues, just comment without blocking
        # Don't approve automatically unless configured to do so
        return (settings.review_auto_approve, False)

    @staticmethod
    def get_decision_summary(approve: bool, request_changes: bool) -> str:
        """
        Get a human-readable summary of the decision.

        Args:
            approve: Whether the PR is approved
            request_changes: Whether changes are requested

        Returns:
            Decision summary text
        """
        if approve:
            return "✅ **Approved** - No significant issues found"
        elif request_changes:
            return "❌ **Changes Requested** - Please address critical/high severity issues"
        else:
            return "💬 **Comments Only** - Review findings provided for consideration"
