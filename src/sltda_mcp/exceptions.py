class AppBaseError(Exception):
    """Base for all sltda-mcp exceptions."""


# Data layer
class DatabaseError(AppBaseError):
    """Raised on unexpected database errors."""


class RecordNotFoundError(AppBaseError):
    """Raised when a requested record does not exist."""


# Ingestion
class IngestionError(AppBaseError):
    """Base for ingestion pipeline errors."""


class DownloadError(IngestionError):
    """Raised when a PDF cannot be downloaded or validated."""


class ParseError(IngestionError):
    """Raised when a PDF cannot be parsed."""


class ExtractionError(IngestionError):
    """Raised when structured data cannot be extracted from a PDF."""


class ValidationError(IngestionError):
    """Raised when extracted data fails schema validation."""


class CutoverError(IngestionError):
    """Raised when the atomic cutover operation fails."""


class FormatUnknownError(IngestionError):
    """Raised when a PDF cannot be classified with sufficient confidence."""


# RAG
class RagError(AppBaseError):
    """Base for RAG pipeline errors."""


class EmbeddingError(RagError):
    """Raised when embedding generation fails."""


class QdrantError(RagError):
    """Raised on Qdrant operation failures."""


class SynthesisError(RagError):
    """Raised when Gemini synthesis fails after retries."""


# MCP tools
class ToolError(AppBaseError):
    """Base for MCP tool errors."""


class InvalidToolInputError(ToolError):
    """Raised when tool input fails validation."""
