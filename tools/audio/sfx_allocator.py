"""SFX placement and automatic generation engine."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from tools.base_tool import (
    BaseTool,
    Determinism,
    ExecutionMode,
    ResourceProfile,
    ToolResult,
    ToolRuntime,
    ToolStability,
    ToolStatus,
    ToolTier,
)
from tools.tool_registry import registry


class SFXAllocator(BaseTool):
    name = "sfx_allocator"
    version = "0.1.0"
    tier = ToolTier.CORE
    capability = "audio_processing"
    provider = "openmontage"
    stability = ToolStability.EXPERIMENTAL
    execution_mode = ExecutionMode.SYNC
    determinism = Determinism.DETERMINISTIC
    runtime = ToolRuntime.LOCAL

    dependencies = []
    install_instructions = "No external dependencies required."
    agent_skills = ["ffmpeg", "elevenlabs", "sound-effects"]

    capabilities = [
        "allocate_sfx",
        "parse_script_cues",
        "generate_and_register_sfx",
    ]

    input_schema = {
        "type": "object",
        "required": ["project_id"],
        "properties": {
            "project_id": {
                "type": "string",
                "description": "The ID/name of the project directory under projects/",
            },
            "sfx_volume_default": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
                "default": 0.7,
                "description": "Default volume for generated sound effects.",
            },
            "override_existing": {
                "type": "boolean",
                "default": False,
                "description": "If true, regenerates and overwrites existing SFX files.",
            },
        },
    }

    resource_profile = ResourceProfile(
        cpu_cores=1, ram_mb=256, vram_mb=0, disk_mb=100, network_required=True
    )

    def get_status(self) -> ToolStatus:
        # Requires sfx_gen to be available
        sfx_gen = registry.get("sfx_gen")
        if sfx_gen and sfx_gen.get_status() == ToolStatus.AVAILABLE:
            return ToolStatus.AVAILABLE
        return ToolStatus.UNAVAILABLE

    def execute(self, inputs: dict[str, Any]) -> ToolResult:
        project_id = inputs["project_id"]
        volume_default = inputs.get("sfx_volume_default", 0.7)
        override = inputs.get("override_existing", False)

        project_dir = Path("projects") / project_id
        if not project_dir.exists():
            return ToolResult(
                success=False,
                error=f"Project directory not found: {project_dir}",
            )

        artifacts_dir = project_dir / "artifacts"
        script_path = artifacts_dir / "script.json"
        manifest_path = artifacts_dir / "asset_manifest.json"
        edit_decisions_path = artifacts_dir / "edit_decisions.json"

        if not script_path.exists():
            return ToolResult(
                success=False,
                error=f"Script artifact not found: {script_path}",
            )

        # 1. Load script
        with open(script_path, encoding="utf-8") as f:
            script_data = json.load(f)

        # 2. Extract SFX cues
        sfx_cues = self._parse_cues(script_data)
        if not sfx_cues:
            return ToolResult(
                success=True,
                data={
                    "message": "No SFX cues found in script.",
                    "sfx_allocated": [],
                },
            )

        # 3. Load asset manifest
        manifest_data = {"version": "1.0", "assets": [], "total_cost_usd": 0.0}
        if manifest_path.exists():
            try:
                with open(manifest_path, encoding="utf-8") as f:
                    manifest_data = json.load(f)
            except Exception as e:
                return ToolResult(
                    success=False,
                    error=f"Failed to load existing asset manifest: {e}",
                )

        # Ensure assets is a list
        if "assets" not in manifest_data:
            manifest_data["assets"] = []

        sfx_gen_tool = registry.get("sfx_gen")
        if not sfx_gen_tool:
            return ToolResult(
                success=False,
                error="sfx_gen tool not found in registry.",
            )

        allocated_sfx = []
        assets_generated = 0
        total_sfx_cost = 0.0

        # Create sfx asset directory
        sfx_dir = project_dir / "assets" / "audio" / "sfx"
        sfx_dir.mkdir(parents=True, exist_ok=True)

        # 4. Generate and register sound effects
        for idx, cue in enumerate(sfx_cues):
            sfx_id = f"sfx_{idx + 1}"
            prompt = cue["prompt"]
            relative_path = f"assets/audio/sfx/{sfx_id}.mp3"
            full_path = project_dir / relative_path

            # Check if asset already exists in manifest
            existing_asset = next(
                (a for a in manifest_data["assets"] if a.get("id") == sfx_id), None
            )

            if existing_asset and not override and full_path.exists():
                # Use existing asset
                allocated_sfx.append({
                    "asset_id": sfx_id,
                    "start_seconds": cue["start_seconds"],
                    "volume": volume_default,
                })
                continue

            # Call sfx_gen tool to generate SFX
            print(f"Generating SFX: '{prompt}' -> {relative_path}")
            res = sfx_gen_tool.execute({
                "prompt": prompt,
                "output_path": str(full_path),
            })

            if not res.success:
                return ToolResult(
                    success=False,
                    error=f"Failed to generate sound effect '{prompt}': {res.error}",
                )

            # Record cost
            cost = res.cost_usd or 0.01
            total_sfx_cost += cost

            # Build asset manifest entry
            asset_entry = {
                "id": sfx_id,
                "type": "sfx",
                "path": relative_path,
                "source_tool": "sfx_gen",
                "scene_id": cue.get("scene_id", "unknown"),
                "prompt": prompt,
                "cost_usd": cost,
                "provider": "elevenlabs",
            }

            # Update manifest_data
            if existing_asset:
                manifest_data["assets"] = [
                    a if a.get("id") != sfx_id else asset_entry
                    for a in manifest_data["assets"]
                ]
            else:
                manifest_data["assets"].append(asset_entry)

            assets_generated += 1
            allocated_sfx.append({
                "asset_id": sfx_id,
                "start_seconds": cue["start_seconds"],
                "volume": volume_default,
            })

        # Save asset manifest
        if "total_cost_usd" in manifest_data:
            manifest_data["total_cost_usd"] = round(
                manifest_data["total_cost_usd"] + total_sfx_cost, 4
            )
        else:
            manifest_data["total_cost_usd"] = round(total_sfx_cost, 4)

        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2)

        # 5. Load and update edit decisions
        edit_decisions_updated = False
        if edit_decisions_path.exists():
            try:
                with open(edit_decisions_path, encoding="utf-8") as f:
                    edit_data = json.load(f)
                
                if "audio" not in edit_data:
                    edit_data["audio"] = {}
                
                # Overwrite or append sfx
                edit_data["audio"]["sfx"] = allocated_sfx
                
                with open(edit_decisions_path, "w", encoding="utf-8") as f:
                    json.dump(edit_data, f, indent=2)
                
                edit_decisions_updated = True
            except Exception as e:
                print(f"Warning: Failed to update edit_decisions: {e}")

        return ToolResult(
            success=True,
            data={
                "message": f"Successfully allocated {len(allocated_sfx)} SFX cues.",
                "assets_generated": assets_generated,
                "total_cost_usd": total_sfx_cost,
                "sfx_allocated": allocated_sfx,
                "edit_decisions_updated": edit_decisions_updated,
            },
            artifacts=[str(manifest_path)] + ([str(edit_decisions_path)] if edit_decisions_updated else []),
        )

    def _parse_cues(self, script_data: dict[str, Any]) -> list[dict[str, Any]]:
        """Parse script sections for SFX tags and return timestamped cues."""
        cues = []
        # Pattern: [sfx: thunder roll] or [sound: wind howling]
        pattern = re.compile(r"\[(?:sfx|sound|bruitage):\s*([^\]]+)\]", re.IGNORECASE)

        sections = script_data.get("sections", [])
        for section in sections:
            section_id = section.get("id", "unknown")
            text = section.get("text", "")
            start = section.get("start_seconds", 0.0)
            end = section.get("end_seconds", start + 5.0)
            duration = end - start

            # Find all bracketed sfx tags
            matches = list(pattern.finditer(text))
            for match in matches:
                prompt = match.group(1).strip()
                # Compute absolute start time based on relative character position in text
                char_idx = match.start()
                text_len = len(text)
                ratio = char_idx / text_len if text_len > 0 else 0.5
                sfx_start = round(start + (duration * ratio), 2)

                cues.append({
                    "prompt": prompt,
                    "start_seconds": sfx_start,
                    "scene_id": section_id,
                })

            # Also check script section enhancement cues
            enhancements = section.get("enhancement_cues", [])
            for cue in enhancements:
                desc = cue.get("description", "")
                # If enhancement cue text describes a sound, e.g. "Sound of wind howling"
                if "sound" in desc.lower() or "sfx" in desc.lower() or "bruit" in desc.lower():
                    # Extract description after "sound of" or "sfx:" or keep description
                    prompt = desc
                    for prefix in ["sound of", "sound:", "sfx:", "bruit de", "bruitage:"]:
                        if desc.lower().startswith(prefix):
                            prompt = desc[len(prefix):].strip()
                            break
                    
                    sfx_start = cue.get("timestamp_seconds", start)
                    cues.append({
                        "prompt": prompt,
                        "start_seconds": sfx_start,
                        "scene_id": section_id,
                    })

        return cues
