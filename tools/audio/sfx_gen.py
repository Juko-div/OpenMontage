"""Sound effect generation tool via ElevenLabs Sound Generation API."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    RetryPolicy,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)


class SFXGen(BaseTool):
    name = "sfx_gen"
    version = "0.1.0"
    tier = ToolTier.GENERATE
    capability = "music_generation"  # part of music and SFX generation capability
    provider = "elevenlabs"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.STOCHASTIC
    runtime = ToolRuntime.API

    dependencies = []  # checked dynamically via API key
    install_instructions = (
        "Set the ELEVENLABS_API_KEY environment variable:\n"
        "  export ELEVENLABS_API_KEY=your_key_here\n"
        "Get a key at https://elevenlabs.io"
    )

    agent_skills = ["elevenlabs", "sound-effects"]

    capabilities = [
        "generate_sfx",
    ]

    input_schema = {
        "type": "object",
        "required": ["prompt"],
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Sound effect description (e.g. 'heavy thunder clap', 'light sci-fi interface chime')",
            },
            "duration_seconds": {
                "type": "number",
                "minimum": 0.5,
                "maximum": 30.0,
                "description": "Duration in seconds (0.5 to 30s). Defaults to auto-calculation.",
            },
            "prompt_influence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.3,
                "description": "How closely to follow the prompt (0.0 to 1.0).",
            },
            "loop": {
                "type": "boolean",
                "default": False,
                "description": "Generate a seamlessly looping sound.",
            },
            "output_path": {"type": "string"},
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=50, network_required=True
    )
    retry_policy = RetryPolicy(max_retries=2, retryable_errors=["rate_limit", "timeout"])
    idempotency_key_fields = ["prompt", "duration_seconds", "prompt_influence", "loop"]
    side_effects = ["writes audio file to output_path", "calls ElevenLabs API"]
    user_visible_verification = [
        "Listen to the generated sound effect for prompt accuracy and audio quality",
    ]

    def get_status(self) -> ToolStatus:
        if os.environ.get("ELEVENLABS_API_KEY"):
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def estimate_cost(self, inputs: dict[str, Any]) -> float:
        # ElevenLabs Sound Gen counts as ~200 characters per sound effect flat rate
        return 0.01

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        api_key = os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            return ToolResult(
                success=False,
                error="No ElevenLabs API key found. " + self.install_instructions,
            )

        start = time.time()
        try:
            result = self._generate(inputs, api_key)
        except Exception as e:
            return ToolResult(success=False, error=f"Sound effect generation failed: {e}")

        result.duration_seconds = round(time.time() - start, 2)
        result.cost_usd = self.estimate_cost(inputs)
        return result

    def _generate(self, inputs: dict[str, Any], api_key: str) -> ToolResult:
        import requests

        prompt = inputs["prompt"]
        duration = inputs.get("duration_seconds")
        influence = inputs.get("prompt_influence", 0.3)
        loop = inputs.get("loop", False)

        url = "https://api.elevenlabs.io/v1/sound-generation"

        headers = {
            "xi-api-key": api_key,
            "Content-Type": "application/json",
        }

        payload: dict[str, Any] = {
            "text": prompt,
            "prompt_influence": influence,
            "loop": loop,
        }
        if duration is not None:
            payload["duration_seconds"] = duration

        response = requests.post(
            url, headers=headers, json=payload, timeout=90
        )
        response.raise_for_status()

        output_path = Path(inputs.get("output_path", "sfx_output.mp3"))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(response.content)

        return ToolResult(
            success=True,
            data={
                "provider": "elevenlabs",
                "prompt": prompt,
                "duration_seconds": duration,
                "loop": loop,
                "output": str(output_path),
                "format": "mp3",
            },
            artifacts=[str(output_path)],
        )
