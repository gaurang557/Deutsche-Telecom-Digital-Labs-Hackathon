import asyncio
import json
import re

from pydantic import ValidationError

from app.config import Settings
from app.planning.exceptions import InvalidPlannerResponseError, PlannerUnavailableError
from app.planning.planner import SYSTEM_PROMPT, Planner
from app.schemas import ActionType, DraftAction, DraftPlan, TaskRequest


class BedrockPlanner:
    """Hosted planning adapter using Amazon Bedrock's Converse API."""

    def __init__(self, settings: Settings) -> None:
        self._region = settings.bedrock_region
        self._model_id = settings.bedrock_model_id

    async def create_draft(self, request: TaskRequest) -> DraftPlan:
        return await asyncio.to_thread(self._create_draft_sync, request)

    def _create_draft_sync(self, request: TaskRequest) -> DraftPlan:
        try:
            import boto3

            client = boto3.client("bedrock-runtime", region_name=self._region)
            response = client.converse(
                modelId=self._model_id,
                system=[
                    {
                        "text": (
                            f"{SYSTEM_PROMPT}\nReturn only JSON matching this schema:\n"
                            f"{json.dumps(DraftPlan.model_json_schema())}"
                        )
                    }
                ],
                messages=[
                    {
                        "role": "user",
                        "content": [{"text": request.text}],
                    }
                ],
                inferenceConfig={"maxTokens": 1600, "temperature": 0},
            )
            content = response["output"]["message"]["content"][0]["text"]
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content.strip())
            return DraftPlan.model_validate_json(content)
        except (KeyError, TypeError, ValidationError, ValueError) as exc:
            raise InvalidPlannerResponseError(
                "The hosted model returned an invalid action plan"
            ) from exc
        except Exception as exc:
            raise PlannerUnavailableError("The hosted planning model is unavailable") from exc


class DemoFallbackPlanner:
    """Small deterministic fallback so the public showcase remains available."""

    async def create_draft(self, request: TaskRequest) -> DraftPlan:
        text = request.text.strip()
        lowered = text.casefold()
        if "list" in lowered and ("file" in lowered or "folder" in lowered):
            action = DraftAction(
                step_key="list",
                type=ActionType.LIST_DIRECTORY,
                target="Downloads",
                description="I’ll show you what is available in the demo Downloads folder.",
                expected_result={"directory_listed": True},
            )
        elif "latest" in lowered and "pdf" in lowered:
            action = DraftAction(
                step_key="open",
                type=ActionType.OPEN_FILE,
                target="Downloads",
                description="I’ll find and open the newest PDF in the demo workspace.",
                parameters={"selection": "latest", "extension": ".pdf"},
                expected_result={"file_opened": True},
            )
        elif "calculator" in lowered:
            action = DraftAction(
                step_key="open",
                type=ActionType.OPEN_APPLICATION,
                target="Calculator",
                description="I’ll open Calculator in the simulated desktop.",
                expected_result={"application": "Calculator", "state": "open"},
            )
        elif "bing" in lowered or "website" in lowered:
            action = DraftAction(
                step_key="open",
                type=ActionType.OPEN_URL,
                target="https://bing.com",
                description="I’ll open Bing in the simulated browser.",
                expected_result={"url_opened": True},
            )
        else:
            action = DraftAction(
                step_key="open",
                type=ActionType.OPEN_APPLICATION,
                target="Files",
                description="I’ll demonstrate that request in the safe desktop sandbox.",
                expected_result={"simulation_completed": True},
            )
        return DraftPlan(
            summary=f"I’ll safely demonstrate this request: {text}",
            actions=[action],
        )


class ResilientHostedPlanner:
    def __init__(self, primary: Planner, fallback: Planner) -> None:
        self._primary = primary
        self._fallback = fallback

    async def create_draft(self, request: TaskRequest) -> DraftPlan:
        try:
            return await self._primary.create_draft(request)
        except (PlannerUnavailableError, InvalidPlannerResponseError):
            return await self._fallback.create_draft(request)
