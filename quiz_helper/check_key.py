"""Gemini API 키 점검 도구.

사용: .\\.venv\\Scripts\\python.exe check_key.py
.env를 어디서 읽었는지, 어떤 키를 읽었는지(앞뒤 4자리), Gemini 호출이 되는지 알려 준다.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from google.genai import errors, types

from gemini_client import (
    APP_DIR,
    MAX_MODEL_SWITCHES,
    GeminiClient,
    GeminiError,
    ModelNotFoundError,
    _client_error,
    load_api_key,
    mask_key,
)


def main() -> int:
    print("=== Quiz Study Helper - API 키 점검 ===\n")

    system_key = os.getenv("GEMINI_API_KEY")
    if system_key:
        print(f"* Windows 환경 변수에도 GEMINI_API_KEY가 있습니다: {mask_key(system_key.strip())}")
        print("  (.env에 키가 있으면 .env 값이 우선합니다)\n")

    env_files = [p for p in dict.fromkeys((Path.cwd() / ".env", APP_DIR / ".env")) if p.is_file()]
    if env_files:
        for p in env_files:
            print(f"* .env 파일 발견: {p}")
    else:
        print(f"* .env 파일이 없습니다: {APP_DIR / '.env'}")
        stray = [p.name for p in APP_DIR.glob(".env*") if p.name != ".env.example"]
        if stray:
            print(f"  대신 이런 파일이 있습니다: {', '.join(stray)}  → 이름을 정확히 .env 로 바꿔 주세요.")

    try:
        key = load_api_key()
    except GeminiError as exc:
        print(f"\n[실패] {exc.title}\n{exc}")
        return 1
    print(f"* 읽은 키: {mask_key(key)}")
    print("  → AI Studio에 보이는 키의 앞뒤 글자와 같은지 확인해 주세요.\n")

    client = GeminiClient(api_key=key)
    print(f"* 설정된 모델: {client.model}")
    print("* 이 키로 쓸 수 있는 모델을 조회하는 중...")
    try:
        available = client.list_models()
    except GeminiError as exc:
        print(f"\n[실패] {exc.title}\n{exc}")
        return 1
    flash = [m for m in available if "flash" in m]
    print(f"  Flash 계열 모델 ({len(flash)}개): {', '.join(flash) or '(없음)'}")
    configured = client.model
    response = None
    for attempt in range(MAX_MODEL_SWITCHES + 1):
        print(f"\n* 모델 {client.model} 에 테스트 요청을 보내는 중...")
        try:
            response = client._client.models.generate_content(
                model=client.model,
                contents="Reply with the single word: OK",
                config=types.GenerateContentConfig(
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True)
                ),
            )
            break
        except errors.ClientError as exc:
            err = _client_error(exc, client.key_hint)
            if not isinstance(err, ModelNotFoundError) or attempt == MAX_MODEL_SWITCHES:
                print(f"\n[실패] {err.title}\n{err}")
                return 1
            print(f"  → '{client.model}'은(는) 쓸 수 없습니다: {(getattr(exc, 'message', '') or '')[:120]}")
            try:
                client.switch_to_available_model(err.recommended)
            except GeminiError as switch_err:
                print(f"\n[실패] {switch_err.title}\n{switch_err}")
                return 1
        except Exception as exc:
            print(f"\n[실패] {exc.__class__.__name__}: {exc}")
            return 1

    if client.model != configured:
        print(f"\n  ! 설정된 모델 '{configured}' 대신 '{client.model}'을(를) 자동으로 사용합니다. (앱도 똑같이 동작합니다)")
        print("    .env에 GEMINI_MODEL 줄이 있다면 지우거나 아래처럼 고치면 매번 바꾸는 과정이 생략됩니다:")
        print(f"    GEMINI_MODEL={client.model}")
    print(f"\n[성공] 모델 {client.model} 응답: {(response.text or '').strip()[:50]}")
    print("키가 정상입니다. 앱에서 다시 시도해 보세요.")
    return 0


if __name__ == "__main__":
    code = main()
    if sys.stdin and sys.stdin.isatty():
        input("\nEnter 키를 누르면 닫힙니다...")
    sys.exit(code)
