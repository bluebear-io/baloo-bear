"""PI-based agent client for code review."""

import logging
from typing import Any

from baloo.agent.config import get_agent_options
from baloo.agent.pi_runtime import PIAgentBase
from baloo.agent.prompts import (
    build_pr_review_prompt,
)
from baloo.agent.schemas import parse_review_output
from baloo.github.models import PRContext, ReviewResult
from baloo.processor.decision_engine import DecisionEngine
from baloo.processor.formatter import CommentFormatter

logger = logging.getLogger(__name__)


class BalooAgent(PIAgentBase):
    """Code review agent powered by PI."""

    def __init__(self, model_override: str = None):
        """Initialize agent with options."""
        options = get_agent_options(model_override)
        super().__init__(options)
        logger.info(f"Initialized BalooAgent with {self.options.model}")

    async def review_pr(
        self,
        pr_context: PRContext,
        model_override: str = None,
        review_id: int | None = None,
        review_logger: Any = None,
    ) -> ReviewResult:
        """
        Perform a full code review for a pull request.

        Args:
            pr_context: Context about the PR including diff and metadata
            model_override: Optional model to use for this review
            review_id: Persist execution logs against this review (DB must be enabled)
            review_logger: Explicit execution logger to use instead of building one
                from the DB. Useful for dry-run/observability tooling that needs to
                capture tool outcomes without a database.

        Returns:
            ReviewResult containing summary, comments, and decision
        """
        if model_override:
            self.options = get_agent_options(model_override)

        logger.info(
            f"Starting review for {pr_context.repo_full_name}#{pr_context.pr_number} using {self.options.model}"
        )

        logger_session = None

        try:
            # Build review prompt
            review_query = build_pr_review_prompt(pr_context)

            # Create execution logger if database is enabled (skipped when one is injected)
            if review_logger is None and review_id:
                from baloo.agent.logger import ReviewLogger
                from baloo.config.settings import get_settings
                from baloo.db.engine import get_session_factory

                settings = get_settings()
                if settings.database_enabled:
                    factory = get_session_factory(settings.database_url)
                    logger_session = factory()
                    review_logger = ReviewLogger(
                        review_id=review_id,
                        session=logger_session,
                        installation_id=settings.installation_id,
                    )

            # Run agent using the provider selected for this deployment.
            structured_data, metadata = await self.run_query(
                review_query, review_logger=review_logger
            )

            # Convert structured output to review comments and general findings
            comments = []
            general_findings = []
            if structured_data is not None:
                comments, general_findings = parse_review_output(structured_data)
            else:
                logger.warning(
                    "No structured output received from agent "
                    "(model: %s, turns: %s, tokens_out: %s, is_error: %s)",
                    metadata.get("model"),
                    metadata.get("num_turns"),
                    metadata.get("output_tokens"),
                    metadata.get("is_error"),
                )
                metadata["agent_error"] = True
                if metadata.get("max_turns_reached"):
                    metadata["error_category"] = "max_turns_reached"
                else:
                    metadata["error_category"] = metadata.get("error_category", "no_output")

            # Generate summary using shared formatter
            summary = CommentFormatter.format_summary(
                comments, metadata, general_findings=general_findings
            )

            # Make approval decision using centralized engine
            approve, request_changes = DecisionEngine.make_decision(
                comments, general_findings=general_findings
            )

            return ReviewResult(
                summary=summary,
                comments=comments,
                general_findings=general_findings,
                approve=approve,
                request_changes=request_changes,
                metadata=metadata,
            )

        except Exception as e:
            logger.error(f"Error during review: {e}", exc_info=True)
            # Return a minimal result with error info and captured metadata (costs)
            metadata = getattr(e, "metadata", {})
            metadata["agent_error"] = True
            metadata["error_category"] = self._classify_error(str(e))
            metadata["error_detail"] = str(e)
            return ReviewResult(
                summary=f"Review failed due to error: {str(e)}",
                comments=[],
                approve=False,
                request_changes=False,
                metadata=metadata,
            )
        finally:
            if logger_session is not None:
                try:
                    await logger_session.commit()
                except Exception as exc:
                    logger.debug("Failed to commit review log session: %s", exc)
                try:
                    await logger_session.close()
                except Exception:
                    pass

    @staticmethod
    def _classify_error(error_msg: str) -> str:
        """Classify an error message into a category for tracking."""
        msg = error_msg.lower()
        if "separator" in msg and ("chunk" in msg or "limit" in msg):
            return "buffer_overflow"
        if "prompt is too long" in msg:
            return "prompt_too_long"
        if "json" in msg and ("parse" in msg or "decode" in msg or "retry" in msg):
            return "json_parse_error"
        if "timeout" in msg or "timed out" in msg:
            return "timeout"
        if "rate limit" in msg or "429" in msg:
            return "rate_limited"
        if "authentication" in msg or "401" in msg or "403" in msg:
            return "auth_error"
        return "agent_error"
