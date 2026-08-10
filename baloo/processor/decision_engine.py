"""Decision engine for PR approval/rejection."""

import logging

from baloo.config.settings import get_settings
from baloo.fidelity.models import FidelityResult
from baloo.github.models import GeneralFinding, ReviewComment
from baloo.processor.severity_router import ReviewSeverity, count_by_severity

logger = logging.getLogger(__name__)


def auto_approve_allowed(repo_full_name: str | None) -> bool:
    """Whether Baloo may post an APPROVE event for this repository.

    Approval is the one review outcome an attacker actively wants, so it needs a
    per-repository decision by a human rather than one global flag across every
    repo an installation serves. Both the master switch and an entry in
    REVIEW_AUTO_APPROVE_REPOS are required.
    """
    settings = get_settings()
    if not settings.review_auto_approve:
        return False

    allowlist = [entry.strip().lower() for entry in settings.review_auto_approve_repos.split(",")]
    allowlist = [entry for entry in allowlist if entry]
    if not allowlist:
        logger.warning(
            "REVIEW_AUTO_APPROVE is enabled but REVIEW_AUTO_APPROVE_REPOS is empty — "
            "no repository has opted in, so nothing will be auto-approved."
        )
        return False

    if "*" in allowlist:
        return True

    if not repo_full_name:
        return False

    repo = repo_full_name.lower()
    owner = repo.split("/", 1)[0]
    return repo in allowlist or f"{owner}/*" in allowlist


class DecisionEngine:
    """Determine whether to approve or request changes on a PR."""

    @staticmethod
    def make_decision(
        comments: list[ReviewComment],
        fidelity_result: FidelityResult | None = None,
        general_findings: list[GeneralFinding] | None = None,
        repo_full_name: str | None = None,
    ) -> tuple[bool, bool]:
        """
        Determine review decision based on findings and fidelity score.

        Args:
            comments: List of review comments
            fidelity_result: Optional fidelity analysis result
            general_findings: Optional list of general (non-inline) findings
            repo_full_name: Repository the PR belongs to, checked against the
                auto-approve allowlist

        Returns:
            Tuple of (approve, request_changes)
        """
        settings = get_settings()
        may_approve = auto_approve_allowed(repo_full_name)

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

        # If fidelity score is high, approve (even with MEDIUM issues).
        # Clean = no CRITICAL or HIGH (we already checked above). The score is
        # itself model output about attacker-supplied content, so it does not
        # get to approve a repo that has not opted in.
        has_high_fidelity = (
            fidelity_result is not None
            and fidelity_result.fidelity_score >= settings.fidelity_approval_threshold
        )

        if has_high_fidelity and may_approve:
            # High fidelity score - approve regardless of MEDIUM issues
            return (True, False)

        # For medium/low issues, just comment without blocking
        # Don't approve automatically unless configured to do so
        return (may_approve, False)

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
