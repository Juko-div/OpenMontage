import json
from pathlib import Path
import pytest
from unittest.mock import MagicMock, patch

from tools.tool_registry import registry
from tools.audio.sfx_gen import SFXGen
from tools.audio.sfx_allocator import SFXAllocator
from tools.base_tool import ToolResult, ToolStatus


def test_sfx_tools_registered():
    registry.discover()
    assert "sfx_gen" in registry.list_all()
    assert "sfx_allocator" in registry.list_all()
    assert isinstance(registry.get("sfx_gen"), SFXGen)
    assert isinstance(registry.get("sfx_allocator"), SFXAllocator)


def test_sfx_allocator_parse_cues():
    allocator = SFXAllocator()
    
    mock_script = {
        "version": "1.0",
        "title": "Test Title",
        "total_duration_seconds": 30.0,
        "sections": [
            {
                "id": "sec_1",
                "text": "A flash of lightning [sfx: lightning strike] illuminates the sky.",
                "start_seconds": 0.0,
                "end_seconds": 10.0,
                "enhancement_cues": []
            },
            {
                "id": "sec_2",
                "text": "Suddenly, a loud blast [sound: explosion] shatters the quiet night.",
                "start_seconds": 10.0,
                "end_seconds": 20.0,
                "enhancement_cues": [
                    {
                        "type": "overlay",
                        "description": "Sound of rain starting",
                        "timestamp_seconds": 15.0
                    }
                ]
            }
        ]
    }
    
    cues = allocator._parse_cues(mock_script)
    assert len(cues) == 3
    
    # First cue from sec_1 bracket [sfx: lightning strike]
    # "A flash of lightning " is 21 characters. Text len is 64. Ratio ~ 0.33
    # start_seconds=0, end_seconds=10. expected ~ 3.3s
    assert cues[0]["prompt"] == "lightning strike"
    assert cues[0]["scene_id"] == "sec_1"
    assert 2.0 <= cues[0]["start_seconds"] <= 4.5
    
    # Second cue from sec_2 bracket [sound: explosion]
    # "Suddenly, a loud blast " is 23 characters. Text len is 68. Ratio ~ 0.34
    # start_seconds=10, end_seconds=20. expected ~ 13.4s
    assert cues[1]["prompt"] == "explosion"
    assert cues[1]["scene_id"] == "sec_2"
    assert 12.0 <= cues[1]["start_seconds"] <= 14.5
    
    # Third cue from sec_2 enhancement_cues
    assert cues[2]["prompt"] == "rain starting"
    assert cues[2]["scene_id"] == "sec_2"
    assert cues[2]["start_seconds"] == 15.0


@patch("tools.audio.sfx_allocator.registry")
def test_sfx_allocator_execute(mock_registry, tmp_path):
    project_id = "test_project"
    project_dir = tmp_path / "projects" / project_id
    project_dir.mkdir(parents=True)
    artifacts_dir = project_dir / "artifacts"
    artifacts_dir.mkdir()
    
    # Create mock script file
    script_path = artifacts_dir / "script.json"
    mock_script = {
        "version": "1.0",
        "title": "Test Title",
        "total_duration_seconds": 10.0,
        "sections": [
            {
                "id": "sec_1",
                "text": "Pressing the button [sfx: high-pitch beep] triggers the flow.",
                "start_seconds": 0.0,
                "end_seconds": 5.0,
            }
        ]
    }
    with open(script_path, "w", encoding="utf-8") as f:
        json.dump(mock_script, f)
        
    # Setup mock sfx_gen tool
    mock_sfx_gen = MagicMock()
    mock_sfx_gen.get_status.return_value = ToolStatus.AVAILABLE
    mock_sfx_gen.execute.return_value = ToolResult(
        success=True,
        data={
            "provider": "elevenlabs",
            "prompt": "high-pitch beep",
            "output": str(project_dir / "assets/audio/sfx/sfx_1.mp3")
        },
        cost_usd=0.01
    )
    
    # Mock registry lookup
    mock_registry.get.side_effect = lambda name: mock_sfx_gen if name == "sfx_gen" else None
    
    # Execute allocator
    allocator = SFXAllocator()
    with patch("tools.audio.sfx_allocator.Path", lambda *args: Path(tmp_path, *args)):
        res = allocator.execute({
            "project_id": project_id,
            "sfx_volume_default": 0.5
        })
        
    assert res.success
    assert res.data["assets_generated"] == 1
    assert len(res.data["sfx_allocated"]) == 1
    assert res.data["sfx_allocated"][0]["asset_id"] == "sfx_1"
    assert res.data["sfx_allocated"][0]["volume"] == 0.5
    
    # Verify manifest was written
    manifest_path = artifacts_dir / "asset_manifest.json"
    assert manifest_path.exists()
    with open(manifest_path, encoding="utf-8") as f:
        manifest_data = json.load(f)
        
    assert len(manifest_data["assets"]) == 1
    assert manifest_data["assets"][0]["id"] == "sfx_1"
    assert manifest_data["assets"][0]["type"] == "sfx"
    assert manifest_data["assets"][0]["path"] == "assets/audio/sfx/sfx_1.mp3"
    assert manifest_data["assets"][0]["cost_usd"] == 0.01
