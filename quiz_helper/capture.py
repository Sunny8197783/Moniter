"""mss를 이용한 화면 영역 캡처."""
from __future__ import annotations

import io
from dataclasses import dataclass

import mss
from PIL import Image


@dataclass(frozen=True)
class CaptureRect:
    """물리(실제) 픽셀 단위의 캡처 영역. 가상 데스크톱 좌표계 기준."""

    left: int
    top: int
    width: int
    height: int


class CaptureError(Exception):
    pass


# mss 10.x부터 mss.mss()가 deprecated 되고 mss.MSS가 도입되었다
_MSS = getattr(mss, "MSS", None) or mss.mss

MIN_SIZE = 10  # 너무 작은 영역은 실수로 클릭한 것으로 간주


def capture_region(rect: CaptureRect) -> Image.Image:
    """지정한 영역을 캡처해 PIL 이미지(RGB)로 반환한다."""
    if rect.width < MIN_SIZE or rect.height < MIN_SIZE:
        raise CaptureError("선택 영역이 너무 작습니다. 다시 드래그해 주세요.")

    monitor = {
        "left": int(rect.left),
        "top": int(rect.top),
        "width": int(rect.width),
        "height": int(rect.height),
    }
    try:
        with _MSS() as sct:
            shot = sct.grab(monitor)
    except Exception as exc:  # mss는 플랫폼별로 다양한 예외를 던진다
        raise CaptureError(f"화면 캡처에 실패했습니다: {exc}") from exc

    return Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")


def image_to_png_bytes(image: Image.Image, max_side: int = 2000) -> bytes:
    """API 전송용 PNG 바이트로 변환한다. 너무 큰 이미지는 축소한다."""
    img = image
    if max(img.size) > max_side:
        img = img.copy()
        img.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()
