"""
Slack notification helpers for operational events.

Configure REFRESH_NOTIFY_SLACK_WEBHOOK in .env with your Incoming Webhook URL.
If the env var is empty, all functions are silent no-ops.

Events covered:
  - Ingestion pipeline complete
  - Ingestion pipeline failed
  - Server health degraded / unhealthy (state-change only, not every poll)

All functions are fire-and-forget: they catch and log on failure but never raise.
Uses stdlib urllib only -- no extra HTTP dependency.
"""

import asyncio
import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone

from sltda_mcp.config import get_settings

logger = logging.getLogger(__name__)


async def _post_slack(payload: dict) -> None:
    """
    POST a JSON payload to the configured Slack Incoming Webhook.
    No-op when REFRESH_NOTIFY_SLACK_WEBHOOK is not set.
    Logs a warning on any error but never raises.
    """
    settings = get_settings()
    webhook_url = settings.refresh_notify_slack_webhook
    if not webhook_url:
        return

    def _send() -> None:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status != 200:
                    logger.warning(
                        "Slack webhook returned non-200 status %s", resp.status
                    )
        except urllib.error.URLError as exc:
            logger.warning("Slack notification failed (non-critical): %s", exc)

    try:
        await asyncio.to_thread(_send)
    except Exception as exc:
        logger.warning("Slack notification task error (non-critical): %s", exc)


async def notify_ingestion_complete(summary: dict) -> None:
    """Send a Slack message when the ingestion pipeline finishes successfully."""
    docs = summary.get("processed", 0)
    chunks = summary.get("chunks_embedded", 0)
    vectors = summary.get("qdrant_points", 0)
    failures = summary.get("parse_failures", 0)
    run_id = summary.get("run_id", "unknown")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":white_check_mark: Ingestion Pipeline Complete",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Documents processed:*\n{docs}"},
                {"type": "mrkdwn", "text": f"*Chunks embedded:*\n{chunks}"},
                {"type": "mrkdwn", "text": f"*Qdrant vectors:*\n{vectors}"},
                {"type": "mrkdwn", "text": f"*Completed at:*\n{ts}"},
            ],
        },
    ]

    if failures:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":warning: *Parse failures:* {failures}",
            },
        })

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"Run ID: `{run_id}`"}],
    })

    await _post_slack({
        "text": ":white_check_mark: SLTDA MCP -- Ingestion Complete",
        "blocks": blocks,
    })


async def notify_ingestion_failed(error: Exception, run_id: str) -> None:
    """Send a Slack message when the ingestion pipeline aborts with an error."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    error_text = str(error)[:600]

    await _post_slack({
        "text": ":x: SLTDA MCP -- Ingestion Failed",
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": ":x: Ingestion Pipeline Failed",
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Error:*\n```{error_text}```",
                },
            },
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Run ID: `{run_id}` | {ts}"}
                ],
            },
        ],
    })


async def notify_health_changed(
    new_status: str,
    components: dict,
) -> None:
    """
    Send a Slack alert when server health transitions to 'degraded' or 'unhealthy'.
    Callers are responsible for state-change gating (call only on transitions).
    """
    if new_status == "unhealthy":
        header_text = ":red_circle: Server Unhealthy"
        text_prefix = ":red_circle: SLTDA MCP -- UNHEALTHY"
    else:
        header_text = ":large_yellow_circle: Server Degraded"
        text_prefix = ":large_yellow_circle: SLTDA MCP -- DEGRADED"

    problem_lines: list[str] = []
    for name, info in components.items():
        status = info.get("status", "unknown")
        if status in ("connected", "ok", "reachable"):
            continue
        detail = info.get("warning") or info.get("error") or ""
        detail_str = f" -- {detail}" if detail else ""
        problem_lines.append(f"* *{name}*: `{status}`{detail_str}")

    problems_text = (
        "\n".join(problem_lines) if problem_lines
        else "_No specific component error reported._"
    )

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    await _post_slack({
        "text": text_prefix,
        "blocks": [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": header_text,
                    "emoji": True,
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*Affected components:*\n{problems_text}",
                },
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": ts}],
            },
        ],
    })
