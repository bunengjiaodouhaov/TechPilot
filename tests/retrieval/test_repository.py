import inspect

from app.retrieval.repository import VectorRepository


def test_vector_repository_declares_async_methods() -> None:
    assert inspect.iscoroutinefunction(VectorRepository.ensure_collection)
    assert inspect.iscoroutinefunction(VectorRepository.upsert_points)
    assert inspect.iscoroutinefunction(VectorRepository.search)


def test_vector_repository_declares_expected_methods() -> None:
    assert hasattr(VectorRepository, "ensure_collection")
    assert hasattr(VectorRepository, "upsert_points")
    assert hasattr(VectorRepository, "search")
