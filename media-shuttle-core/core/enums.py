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


class SourceSite(str, Enum):
    GOFILE = "GOFILE"
    BUNKR = "BUNKR"
    CYBERDROP = "CYBERDROP"
    CYBERFILE = "CYBERFILE"
    FILESTER = "FILESTER"
    PIXELDRAIN = "PIXELDRAIN"
    GD = "GD"
    MEGA = "MEGA"
    MEDIAFIRE = "MEDIAFIRE"
    SAINT = "SAINT"
    TRANSFERIT = "TRANSFERIT"
    TURBO = "TURBO"
    COOMER = "COOMER"
    YTDL = "YTDL"
    GENERIC = "GENERIC"


def default_site_queue_suffixes() -> list[str]:
    return [
        SourceSite.GOFILE.value,
        SourceSite.BUNKR.value,
        SourceSite.CYBERDROP.value,
        SourceSite.CYBERFILE.value,
        SourceSite.FILESTER.value,
        SourceSite.PIXELDRAIN.value,
        SourceSite.GD.value,
        SourceSite.MEGA.value,
        SourceSite.MEDIAFIRE.value,
        SourceSite.SAINT.value,
        SourceSite.TRANSFERIT.value,
        SourceSite.TURBO.value,
        SourceSite.COOMER.value,
        SourceSite.YTDL.value,
        SourceSite.GENERIC.value,
    ]
