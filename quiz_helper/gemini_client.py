"""Gemini API 호출, JSON 파싱, 1회 재시도."""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

APP_DIR = Path(__file__).resolve().parent
DEFAULT_MODEL = "gemini-2.5-flash"
REQUEST_TIMEOUT_MS = 60_000

SYSTEM_PROMPT = (
    "이미지 속 객관식 문제를 읽고 JSON으로만 답하라:\n"
    '{"question_summary": str, "answer": "보기 번호와 내용", '
    '"explanation": "풀이 근거 2~4문장", "confidence": "high|medium|low"}\n'
    "이미지에서 객관식 문제를 찾을 수 없거나 글자를 읽을 수 없으면 "
    'answer를 빈 문자열("")로, confidence를 "low"로 하고 question_summary에 이유를 적어라.'
)
USER_PROMPT = "이 이미지의 객관식 문제를 풀어 줘."

VALID_CONFIDENCE = ("high", "medium", "low")


# ---------------------------------------------------------------- 결과/예외
@dataclass
class QuizResult:
    question_summary: str
    answer: str
    explanation: str
    confidence: str  # high | medium | low
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    @property
    def needs_check(self) -> bool:
        return self.confidence == "low"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "QuizResult":
        return cls(
            question_summary=str(data.get("question_summary", "")),
            answer=str(data.get("answer", "")),
            explanation=str(data.get("explanation", "")),
            confidence=str(data.get("confidence", "low")),
            created_at=str(data.get("created_at", "")),
        )


class GeminiError(Exception):
    """사용자에게 그대로 보여줄 수 있는 메시지를 가진 예외."""

    title = "오류"
    retryable = False


class MissingApiKeyError(GeminiError):
    title = "API 키 없음"


class InvalidApiKeyError(GeminiError):
    title = "API 키 오류"


class NetworkError(GeminiError):
    title = "네트워크 오류"
    retryable = True


class ApiServiceError(GeminiError):
    title = "API 오류"
    retryable = True


class ModelNotFoundError(GeminiError):
    title = "모델 오류"


class RecognitionError(GeminiError):
    title = "문제 인식 실패"
    retryable = True  # 응답 형식이 깨진 경우 한 번 더 시도할 가치가 있다


# ---------------------------------------------------------------- 파싱
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


def parse_response(text: str | None) -> QuizResult:
    """모델 응답 텍스트를 QuizResult로 변환한다. 실패 시 RecognitionError."""
    if not text or not text.strip():
        raise RecognitionError("모델이 빈 응답을 반환했습니다.")

    raw = _FENCE_RE.sub("", text.strip())
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # 앞뒤에 설명이 붙은 경우 첫 번째 { ... } 블록만 추출
        start, end = raw.find("{"), raw.rfind("}")
        if start == -1 or end <= start:
            raise RecognitionError("응답에서 JSON을 찾을 수 없습니다.") from None
        try:
            data = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise RecognitionError("응답 JSON 형식이 올바르지 않습니다.") from exc

    if isinstance(data, list) and data:
        data = data[0]
    if not isinstance(data, dict):
        raise RecognitionError("응답 JSON 형식이 올바르지 않습니다.")

    answer = str(data.get("answer") or "").strip()
    summary = str(data.get("question_summary") or "").strip()
    if not answer:
        reason = f" ({summary})" if summary else ""
        # 모델이 '문제가 없다'고 판단한 것이므로 재시도하지 않는다
        err = RecognitionError(f"이미지에서 객관식 문제를 인식하지 못했습니다{reason}. 영역을 다시 선택해 주세요.")
        err.retryable = False
        raise err

    confidence = str(data.get("confidence") or "low").strip().lower()
    if confidence not in VALID_CONFIDENCE:
        confidence = "low"

    return QuizResult(
        question_summary=summary or "(요약 없음)",
        answer=answer,
        explanation=str(data.get("explanation") or "").strip() or "(해설 없음)",
        confidence=confidence,
    )


# ---------------------------------------------------------------- 모델 선택
# 이미지 문제 풀이에 맞지 않는 특수 용도 모델은 자동 선택에서 제외한다
_EXCLUDED_MODEL_WORDS = (
    "image", "tts", "audio", "live", "embedding", "native", "robotics", "computer-use", "8b", "learnlm", "gemma",
)
_VERSION_RE = re.compile(r"gemini-(\d+(?:\.\d+)?)")


def normalize_model_name(name: str) -> str:
    """'models/Gemini 2.5 Flash' 같은 입력을 'gemini-2.5-flash' 형태로 정리한다."""
    name = name.strip().strip("\"'").lower()
    name = name.removeprefix("models/")
    name = re.sub(r"(\d),(\d)", r"\1.\2", name)  # 2,5 → 2.5
    name = re.sub(r"[\s_]+", "-", name)
    if name and name[0].isdigit():
        name = "gemini-" + name
    return name


def _model_rank(name: str) -> tuple:
    """클수록 우선: 안정판 > 미리보기, Flash > Flash-Lite > 기타, 높은 버전 우선."""
    match = _VERSION_RE.match(name)
    version = float(match.group(1)) if match else 0.0
    stable = not any(w in name for w in ("preview", "exp"))
    is_alias = name.endswith("-latest")
    if "flash" in name and "lite" not in name:
        family = 2
    elif "flash" in name:
        family = 1
    else:
        family = 0
    return (family, stable, not is_alias, version, name)


def choose_model(available: list[str], preferred: str | None = None) -> str | None:
    """사용 가능한 모델 중 preferred가 있으면 그것을, 없으면 가장 알맞은 모델을 고른다."""
    if preferred and preferred in available:
        return preferred
    candidates = [
        m for m in available
        if m.startswith("gemini") and not any(w in m for w in _EXCLUDED_MODEL_WORDS)
    ]
    if not candidates:
        return None
    return max(candidates, key=_model_rank)


# ---------------------------------------------------------------- 클라이언트
def load_api_key() -> str:
    """GEMINI_API_KEY를 읽는다. 우선순위: quiz_helper/.env > 현재 폴더 .env > 환경 변수.

    호출할 때마다 .env를 다시 읽으므로 앱 실행 중에 키를 고쳐도 반영된다.
    """
    for env_file in (Path.cwd() / ".env", APP_DIR / ".env"):
        if env_file.is_file():
            load_dotenv(env_file, override=True)
    key = os.getenv("GEMINI_API_KEY", "").strip().strip("\"'").strip()
    if not key or key.startswith("your_"):
        raise MissingApiKeyError(
            "GEMINI_API_KEY가 설정되지 않았습니다.\n"
            ".env.example을 .env로 복사해 키를 입력한 뒤 다시 시도해 주세요."
        )
    if not key.isascii() or any(ch.isspace() for ch in key):
        raise InvalidApiKeyError(
            "API 키에 공백이나 한글 등 잘못된 문자가 섞여 있습니다.\n"
            f"AI Studio에서 키를 다시 복사해 .env에 붙여 넣어 주세요. (현재 값: {mask_key(key)})"
        )
    return key


def mask_key(key: str) -> str:
    """화면에 보여 줄 수 있도록 키의 앞뒤 4자리만 남긴다."""
    if len(key) <= 8:
        return f"{'*' * len(key)} ({len(key)}자)"
    return f"{key[:4]}…{key[-4:]} ({len(key)}자)"


class GeminiClient:
    def __init__(self, api_key: str | None = None, model: str | None = None, client=None):
        self.model = normalize_model_name(model or os.getenv("GEMINI_MODEL", "") or DEFAULT_MODEL)
        self.key_hint = ""
        self.model_switched_from: str | None = None  # 설정한 모델이 없어 자동으로 바꾼 경우 원래 이름
        if client is not None:  # 테스트용 주입
            self._client = client
            return
        api_key = api_key or load_api_key()
        self.key_hint = mask_key(api_key)
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(
                timeout=REQUEST_TIMEOUT_MS,
                retry_options=types.HttpRetryOptions(attempts=1),  # 재시도는 아래에서 직접 1회만
            ),
        )

    def analyze(self, png_bytes: bytes) -> QuizResult:
        """이미지를 보내 결과를 받는다.

        - 재시도 가능한 오류는 1회 재시도한다.
        - 모델을 찾을 수 없으면 이 키로 쓸 수 있는 모델을 조회해 자동으로 바꾼 뒤 다시 시도한다.
        """
        try:
            return self._analyze_with_retry(png_bytes)
        except ModelNotFoundError:
            self.switch_to_available_model()
        return self._analyze_with_retry(png_bytes)

    def _analyze_with_retry(self, png_bytes: bytes) -> QuizResult:
        try:
            return self._analyze_once(png_bytes)
        except GeminiError as exc:
            if not exc.retryable:
                raise
        return self._analyze_once(png_bytes)

    def list_models(self) -> list[str]:
        """이 키로 generateContent를 호출할 수 있는 모델 이름 목록."""
        try:
            models = list(self._client.models.list(config={"page_size": 1000}))
        except errors.ClientError as exc:
            raise _client_error(exc, self.key_hint) from exc
        except errors.ServerError as exc:
            raise ApiServiceError(f"Gemini 서버 오류({exc.code})입니다. 잠시 후 다시 시도해 주세요.") from exc
        except (httpx.TransportError, ConnectionError, TimeoutError) as exc:
            raise NetworkError("Gemini 서버에 연결할 수 없습니다. 인터넷 연결을 확인해 주세요.") from exc
        names = []
        for m in models:
            actions = getattr(m, "supported_actions", None)
            if actions and "generateContent" not in actions:
                continue
            names.append((m.name or "").removeprefix("models/"))
        return sorted(n for n in names if n)

    def switch_to_available_model(self) -> str:
        """현재 모델을 쓸 수 없을 때 사용 가능한 모델로 바꾼다. 바꾼 모델 이름을 반환한다."""
        available = self.list_models()
        chosen = choose_model(available, self.model)
        if chosen is None:
            sample = ", ".join(available[:8]) or "(없음)"
            raise ModelNotFoundError(
                f"'{self.model}' 모델을 쓸 수 없고, 이 API 키로 사용할 수 있는 Gemini 모델도 찾지 못했습니다.\n"
                f"사용 가능한 모델: {sample}"
            )
        if chosen != self.model:
            self.model_switched_from = self.model
            self.model = chosen
        return chosen

    def _analyze_once(self, png_bytes: bytes) -> QuizResult:
        try:
            response = self._client.models.generate_content(
                model=self.model,
                contents=[
                    types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
                    USER_PROMPT,
                ],
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    temperature=0.2,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
        except errors.ClientError as exc:
            raise _client_error(exc, self.key_hint) from exc
        except errors.ServerError as exc:
            raise ApiServiceError(f"Gemini 서버 오류({exc.code})입니다. 잠시 후 다시 시도해 주세요.") from exc
        except (httpx.TransportError, ConnectionError, TimeoutError) as exc:
            raise NetworkError("Gemini 서버에 연결할 수 없습니다. 인터넷 연결을 확인해 주세요.") from exc

        return parse_response(getattr(response, "text", None))


def _client_error(exc: errors.ClientError, key_hint: str = "") -> GeminiError:
    """Google의 거부 사유를 구분해 사용자가 할 일을 알려 준다. 원래 응답도 함께 보여 준다."""
    raw = f"{getattr(exc, 'message', '') or ''} {exc}"
    lowered = raw.lower()
    google_says = f"\n\nGoogle 응답({exc.code} {getattr(exc, 'status', '') or ''}): {(getattr(exc, 'message', '') or str(exc))[:200]}"
    key_line = f"\n앱이 읽은 키: {key_hint}" if key_hint else ""

    if "leaked" in lowered:
        return InvalidApiKeyError(
            "이 API 키는 유출된 키로 신고되어 Google이 차단했습니다.\n"
            "AI Studio에서 새 키를 발급해 .env에 넣어 주세요." + key_line + google_says
        )
    if "api_key_invalid" in lowered or "api key not valid" in lowered or "api key expired" in lowered:
        return InvalidApiKeyError(
            "키 값이 올바르지 않거나 만료되었습니다.\n"
            "AI Studio(aistudio.google.com/apikey)에서 키 전체를 다시 복사해 .env에 붙여 넣어 주세요."
            + key_line + google_says
        )
    if "service_disabled" in lowered or "has not been used in project" in lowered or "is disabled" in lowered:
        return InvalidApiKeyError(
            "이 키가 속한 프로젝트에서 Gemini API가 꺼져 있습니다.\n"
            "AI Studio에서 새 키를 만들거나, Google Cloud Console에서 'Generative Language API'를 사용 설정해 주세요."
            + key_line + google_says
        )
    if "blocked" in lowered or "referer" in lowered or "ip address" in lowered or "restrict" in lowered:
        return InvalidApiKeyError(
            "API 키에 사용 제한(허용 API·IP·웹사이트 제한)이 걸려 있습니다.\n"
            "Google Cloud Console의 사용자 인증 정보에서 제한을 풀거나, AI Studio에서 새 키를 만들어 주세요."
            + key_line + google_says
        )
    if "location is not supported" in lowered:
        return GeminiError(
            "현재 접속 지역에서는 Gemini API를 쓸 수 없습니다. VPN을 쓰고 있다면 끄고 다시 시도해 주세요." + google_says
        )
    if exc.code in (401, 403):
        return InvalidApiKeyError("API 키 권한이 거부되었습니다. 아래 Google 응답을 확인해 주세요." + key_line + google_says)
    if exc.code == 429:
        err = ApiServiceError(
            "요청 한도를 초과했습니다. 잠시 후 다시 시도해 주세요.\n"
            "(무료 등급은 분당·일일 요청 수 제한이 있습니다.)" + google_says
        )
        err.retryable = False
        return err
    if exc.code == 404 or ("model" in lowered and ("not found" in lowered or "not supported" in lowered)):
        return ModelNotFoundError("모델을 찾을 수 없습니다. .env의 GEMINI_MODEL 설정을 확인해 주세요." + google_says)
    return GeminiError(f"요청이 거부되었습니다({exc.code})." + google_says)
