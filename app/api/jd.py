from fastapi import APIRouter, Depends

from app.api.dependencies import get_jd_extractor
from app.api.schemas.jd import JDAnalyzeRequest
from app.jd.deepseek_extractor import DeepSeekJDExtractor
from app.jd.schemas import StructuredJD


router = APIRouter(prefix="/jd", tags=["jd"])


@router.post("/analyze", response_model=StructuredJD)
async def analyze_jd(
    request: JDAnalyzeRequest,
    extractor: DeepSeekJDExtractor = Depends(get_jd_extractor),
) -> StructuredJD:
    return await extractor.extract(request.jd)
