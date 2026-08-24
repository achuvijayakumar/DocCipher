"""Pydantic models for the DocCipher Breaker API."""

from typing import Optional

from pydantic import BaseModel, Field


class LogLine(BaseModel):
    message: str
    level: str = "info"


class CrackResponse(BaseModel):
    status: str
    input_name: Optional[str] = None
    input_path: Optional[str] = None
    output_name: Optional[str] = None
    output_path: Optional[str] = None
    error: Optional[str] = None
    logs: list[str] = Field(default_factory=list)
    size_before: int = 0
    size_after: int = 0
    duration: float = 0.0
    protections_found: int = 0
    failed_step: Optional[int] = None
    format: str = "docx"
    method: Optional[str] = None
    history_id: Optional[int] = None
    download_token: Optional[str] = None


class BatchRequest(BaseModel):
    paths: list[str] = Field(..., min_length=1, max_length=200)
    output_dir: Optional[str] = None


class BatchResponse(BaseModel):
    total: int
    succeeded: int
    failed: int
    results: list[CrackResponse]


class InspectResponse(BaseModel):
    format: str = "docx"
    protected: bool
    # DOCX-specific
    edit_mode: Optional[str] = None
    enforced: bool = False
    password_hashed: bool = False
    # PDF-specific
    needs_password: bool = False
    restrictions: list[str] = Field(default_factory=list)
    can_unlock: bool = True
    pages: Optional[int] = None


class HistoryEntry(BaseModel):
    id: int
    original_filename: str
    original_path: Optional[str] = None
    unlocked_filename: Optional[str] = None
    unlocked_path: Optional[str] = None
    file_size_before: int = 0
    file_size_after: int = 0
    status: str
    error: Optional[str] = None
    duration: float = 0.0
    protections_found: int = 0
    file_format: str = "docx"
    method: Optional[str] = None
    timestamp: str


class StatsResponse(BaseModel):
    total: int = 0
    successes: int = 0
    failures: int = 0
    protections: int = 0
    avg_duration: float = 0.0
    bytes_saved: int = 0
    docx_count: int = 0
    pdf_count: int = 0
    xlsx_count: int = 0
