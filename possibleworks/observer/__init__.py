"""
Possibleworks Observer Package

A generic workflow event observer framework for Frappe/ERPNext.

This package provides:
- Event listening for all document changes
- Workflow extraction and metadata
- Event payload building
- Redis-based event buffering
- Batch processing to external APIs

Main components:
- observer.py: Event listener
- payload_builder.py: JSON payload construction
- workflow_service.py: Workflow metadata extraction
- redis_buffer_service.py: Event queuing
- batch_processor.py: External API integration
"""

from .observer import (
    WorkflowEventObserver,
    handle_after_insert,
    handle_on_update,
    handle_on_submit,
    handle_on_cancel,
    handle_before_insert,
    handle_before_update,
)
from .payload_builder import PayloadBuilder
from .workflow_service import WorkflowService
from .redis_buffer_service import RedisBufferService
from .batch_processor import BatchProcessor
from .settings_helper import SettingsHelper

__version__ = "1.0.0"
__all__ = [
    "WorkflowEventObserver",
    "PayloadBuilder",
    "WorkflowService",
    "RedisBufferService",
    "BatchProcessor",
    "SettingsHelper",
]