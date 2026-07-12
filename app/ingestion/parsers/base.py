from abc import ABC, abstractmethod

from app.ingestion.schemas import ParseInput, ParsedDocument


class BaseParser(ABC):
    """Contract implemented by every supported document parser."""

    @abstractmethod
    def parse(self, parse_input: ParseInput) -> ParsedDocument:
        """Convert validated file data into TechPilot's internal representation."""
