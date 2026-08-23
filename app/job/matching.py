from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.jd.normalizer import SkillNormalizer
from app.jd.schemas import RequirementType, StructuredJD

from .profile import UserCapabilityProfile


class JobMatchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0, le=1)
    matched_skills: list[str] = Field(default_factory=list)
    missing_required_skills: list[str] = Field(default_factory=list)
    missing_preferred_skills: list[str] = Field(default_factory=list)


class JobMatcher:
    """Deterministic baseline for optional resume/profile-based ranking."""

    def __init__(self, normalizer: SkillNormalizer | None = None) -> None:
        self._normalizer = normalizer or SkillNormalizer()

    def match(
        self,
        *,
        jd: StructuredJD,
        profile: UserCapabilityProfile,
    ) -> JobMatchReport:
        user_skills = {
            self._normalizer.normalize(skill).canonical_name.casefold()
            for skill in profile.skills
        }

        scored: list[tuple[str, RequirementType, bool]] = []
        for requirement in jd.requirements:
            if not requirement.normalized_skill:
                continue
            canonical = self._normalizer.normalize(
                requirement.normalized_skill
            ).canonical_name
            scored.append(
                (
                    canonical,
                    requirement.requirement_type,
                    canonical.casefold() in user_skills,
                )
            )

        if not scored:
            return JobMatchReport(score=0.0)

        # Required requirements carry twice the weight of preferred/unclear.
        total_weight = 0.0
        matched_weight = 0.0
        matched: list[str] = []
        missing_required: list[str] = []
        missing_preferred: list[str] = []

        for skill, requirement_type, is_match in scored:
            weight = 2.0 if requirement_type is RequirementType.REQUIRED else 1.0
            total_weight += weight
            if is_match:
                matched_weight += weight
                matched.append(skill)
            elif requirement_type is RequirementType.REQUIRED:
                missing_required.append(skill)
            else:
                missing_preferred.append(skill)

        return JobMatchReport(
            score=matched_weight / total_weight,
            matched_skills=matched,
            missing_required_skills=missing_required,
            missing_preferred_skills=missing_preferred,
        )
