"""Unit test: consecutive prompt-template versions are never byte-identical (task 4.5).

Requirement 2.3: a new Prompt_Template version must not be introduced with
content byte-identical to the immediately preceding version of the same
LLM_Feature — a version bump without a content change would make the
LLM_Invocation_Log's recorded prompt version meaningless for Phase 5 replay.

The test discovers every ``<feature_name>.v<N>.txt`` file shipped in the
prompts package and compares each consecutive version pair as raw bytes.
With only ``v1`` files present the pair comparison is vacuous, so the test
also asserts each feature ships at least one template (guarding the
discovery logic itself against silently matching nothing).
"""

from __future__ import annotations

import re
from importlib import resources
from importlib.resources.abc import Traversable

from matchlayer_api.ml.prompts.registry import LLMFeature

_PROMPTS_PACKAGE = "matchlayer_api.ml.prompts"


def _template_versions(feature: LLMFeature) -> dict[int, Traversable]:
    """Map version number → template file for every shipped version of ``feature``."""
    pattern = re.compile(rf"^{re.escape(feature.value)}\.v(\d+)\.txt$")
    versions: dict[int, Traversable] = {}
    for entry in resources.files(_PROMPTS_PACKAGE).iterdir():
        match = pattern.match(entry.name)
        if match is not None:
            versions[int(match.group(1))] = entry
    return versions


class TestConsecutivePromptVersionsDiffer:
    def test_every_feature_ships_at_least_one_template(self) -> None:
        for feature in LLMFeature:
            assert _template_versions(feature), (
                f"no template files found for feature {feature.value!r}"
            )

    def test_consecutive_versions_are_not_byte_identical(self) -> None:
        for feature in LLMFeature:
            versions = _template_versions(feature)
            for version in sorted(versions):
                previous = versions.get(version - 1)
                if previous is None:
                    continue
                current_bytes = versions[version].read_bytes()
                previous_bytes = previous.read_bytes()
                assert current_bytes != previous_bytes, (
                    f"{feature.value}.v{version}.txt is byte-identical to "
                    f"{feature.value}.v{version - 1}.txt (Requirement 2.3)"
                )
