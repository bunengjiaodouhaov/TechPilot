from app.jd.normalizer import SkillNormalizer


def test_normalizer_is_conservative():
    normalizer = SkillNormalizer()

    assert normalizer.normalize("retrieval augmented generation").canonical_name == "RAG"
    assert normalizer.normalize("FastAPI").canonical_name == "FastAPI"
    assert normalizer.normalize("Some New Skill").canonical_name == "Some New Skill"
