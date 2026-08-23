from app.job.profile import (
    UserCapabilityProfile,
    UserSkill,
    SkillLevel,
)

from app.resume.extractor import (
    ResumeExtractor,
)



class ResumeProfileService:


    def __init__(
        self,
        extractor: ResumeExtractor,
    ):

        self.extractor = extractor



    async def build_profile(
        self,
        text: str,
    ) -> UserCapabilityProfile:

        resume = await (
            self.extractor.extract(
                text
            )
        )


        return UserCapabilityProfile(
            skills=[
                UserSkill(
                    name=item.name,
                    level=SkillLevel(
                        item.level
                    ),
                )
                for item in resume.skills
            ]
        )
