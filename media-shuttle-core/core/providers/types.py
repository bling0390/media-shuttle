from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ..models import DownloadResult, ParsedSource, UploadResult

ParserFn = Callable[[str], list[ParsedSource]]
MatcherFn = Callable[[str], bool]
DownloaderFn = Callable[[ParsedSource], DownloadResult]
SiteMatcher = Callable[[ParsedSource], bool]
UploaderFn = Callable[[DownloadResult, str], UploadResult]
TargetMatcher = Callable[[str], bool]


@dataclass
class ParseProvider:
    name: str
    mode: str  # mock | live | all
    matcher: MatcherFn
    parser: ParserFn


@dataclass
class DownloadProvider:
    name: str
    mode: str  # mock | live | all
    matcher: SiteMatcher
    downloader: DownloaderFn


@dataclass
class UploadProvider:
    name: str
    mode: str  # mock | live | all
    matcher: TargetMatcher
    uploader: UploaderFn
