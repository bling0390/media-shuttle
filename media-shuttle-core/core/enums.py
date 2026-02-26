from enum import Enum


class TaskStatus(str, Enum):
    QUEUED = "QUEUED"
    PARSING = "PARSING"
    DOWNLOADING = "DOWNLOADING"
    UPLOADING = "UPLOADING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class UploadTarget(str, Enum):
    RCLONE = "RCLONE"
    TELEGRAM = "TELEGRAM"
