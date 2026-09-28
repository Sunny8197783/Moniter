# 📘 Quiz Study Helper

화면에 보이는 **객관식 문제**를 드래그로 캡처하면 **Gemini API**(gemini-2.5-flash)가
정답·해설·신뢰도를 알려 주는 Windows용 학습 도우미입니다.
최근 10개 결과는 히스토리 탭에 저장되어 오답노트로 활용할 수 있습니다.

> 스스로 공부한 뒤 채점하거나 오답을 복습할 때 쓰는 용도입니다.
> 시험·평가 중 사용이 금지된 환경에서는 사용하지 마세요.

---

## 1. 준비물

- Windows 10/11
- Python **3.11 이상** ([python.org](https://www.python.org/downloads/)에서 설치 시 *"Add python.exe to PATH"* 체크)
- Gemini API 키 ([Google AI Studio](https://aistudio.google.com/apikey)에서 무료 발급)

---

## 2. 설치부터 실행까지

아래 명령은 **PowerShell** 기준입니다. `quiz_helper` 폴더에서 실행하세요.

### ① 가상환경 생성 및 활성화

```powershell
cd quiz_helper
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

> `Activate.ps1`을 실행할 수 없다는 오류가 나면 한 번만 아래 명령을 실행한 뒤 다시 시도하세요.
> ```powershell
> Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
> ```
> 명령 프롬프트(cmd)를 쓴다면 `.venv\Scripts\activate.bat` 을 실행합니다.

활성화되면 프롬프트 앞에 `(.venv)`가 표시됩니다.

### ② 패키지 설치

```powershell
pip install -r requirements.txt
```

### ③ `.env`에 API 키 입력

```powershell
copy .env.example .env
notepad .env
```

메모장에서 `your_api_key_here`를 발급받은 키로 바꾸고 저장합니다.

```
GEMINI_API_KEY=AIza...실제키...
```

> 키는 코드에 넣지 않고 `.env`에서만 읽습니다. `.env`는 `.gitignore`에 포함되어 있으니 절대 공유하지 마세요.
> 앱 실행 중에 `.env`를 고쳐도 다음 요청부터 바로 반영됩니다.

### ④ 실행

```powershell
python main.py
```

작은 **Quiz Study Helper** 창이 열리고, 작업 표시줄 오른쪽 **시스템 트레이**에 파란색 **Q** 아이콘이 나타납니다.

### ⑤ 바탕화면 바로가기 만들기 (선택)

`quiz_helper` 폴더의 **`create_shortcut.bat`을 더블클릭**하면 바탕화면에 **Quiz Study Helper** 아이콘이 생깁니다.
이후에는 PowerShell 없이 **바탕화면 아이콘을 더블클릭**하면 콘솔 창 없이 바로 실행됩니다.

- ①~② 단계(가상환경 생성, 패키지 설치)를 먼저 마쳐야 합니다. 안 했다면 스크립트가 안내 메시지를 띄웁니다.
- 전역 단축키를 위해 **관리자 권한으로 실행되는 바로가기**가 필요하면, `quiz_helper` 폴더에서 PowerShell을 열고
  `.\create_shortcut.bat admin` 을 실행합니다. (실행할 때마다 Windows 확인 창이 뜹니다.)
- `quiz_helper` 폴더를 다른 곳으로 옮겼다면 바로가기가 끊어지니, 옮긴 위치에서 `create_shortcut.bat`을 다시 실행하세요.
- "Windows의 PC 보호" 창이 뜨면 **추가 정보 → 실행**을 누르세요. (직접 받은 스크립트라서 뜨는 경고입니다.)

---

## 3. 사용법

### 단축키

| 동작 | 방법 |
| --- | --- |
| 영역 선택 시작 | **`Ctrl + Shift + Q`** (어느 창에서든 동작) 또는 창의 **"영역 선택"** 버튼, 트레이 메뉴 |
| 영역 지정 | 마우스 **왼쪽 버튼으로 드래그** |
| 선택 취소 | **`ESC`** 또는 마우스 오른쪽 클릭 |

### 순서

1. 문제가 보이는 화면(브라우저, PDF 등)을 띄웁니다.
2. `Ctrl + Shift + Q`를 누르면 화면 전체가 어둡게 변합니다.
3. **문제와 보기가 모두 들어가도록** 드래그합니다. 선택한 영역은 파란 테두리로 표시됩니다.
4. 마우스를 놓으면 캡처 후 분석이 시작되고, 화면 **우측 하단 카드**에 결과가 표시됩니다.
   - **정답**: 보기 번호와 내용
   - **해설**: 풀이 근거 2~4문장
   - **신뢰도**: 높음 / 보통 / 낮음 — **낮음이면 "⚠ 직접 확인 필요" 경고**가 표시됩니다.
5. 카드는 오른쪽 위 **✕** 버튼으로 닫습니다. (카드는 드래그해서 옮길 수도 있습니다.)

### 히스토리 (오답노트)

- 컨트롤 창의 **히스토리** 탭에서 최근 **10개** 결과(문제 요약, 정답, 해설)를 볼 수 있습니다.
- 항목을 클릭하면 상세 내용이 아래에 표시됩니다. "기록 전체 삭제"로 비울 수 있습니다.
- 저장 위치: `%APPDATA%\QuizStudyHelper\history.json`

### 창 닫기 / 종료

- 컨트롤 창의 X를 누르면 **트레이로 숨겨지고** 단축키는 계속 동작합니다.
- 트레이 아이콘 클릭 → 창 다시 열기, 우클릭 메뉴 → **종료**.

---

## 4. 오류 메시지

오류가 나면 결과 카드에 원인이 표시됩니다.

| 카드 메시지 | 원인 / 해결 |
| --- | --- |
| **API 키 없음** | `.env` 파일이 없거나 `GEMINI_API_KEY`가 비어 있음 → 2-③ 단계 확인 |
| **API 키 오류** | 키가 잘못됨 → AI Studio에서 키를 다시 복사 |
| **네트워크 오류** | 인터넷 연결 끊김/시간 초과 (자동으로 1회 재시도 후 표시) |
| **API 오류** | Gemini 서버 오류 또는 요청 한도 초과 → 잠시 후 다시 시도 |
| **문제 인식 실패** | 선택 영역에 객관식 문제가 없거나 글자가 너무 작음 → 문제와 보기를 모두 포함해 더 크게 선택 |
| **캡처 실패** | 선택 영역이 너무 작음 → 다시 드래그 |

## 5. 문제 해결

- **`Ctrl+Shift+Q`가 안 먹어요**: 컨트롤 창 하단에 "전역 단축키 등록 실패"가 보이면
  PowerShell을 **관리자 권한으로 실행**한 뒤 다시 실행하세요. 관리자 권한으로 실행 중인 프로그램(일부 게임·보안 프로그램) 위에서는
  이 앱도 관리자 권한이어야 단축키가 전달됩니다. 창의 "영역 선택" 버튼은 항상 사용할 수 있습니다.
- **모니터 배율(125%, 150%)이나 모니터가 여러 대일 때**: 모니터별 배율을 반영해 캡처하므로 그대로 사용하면 됩니다.
- **바탕화면 아이콘을 눌러도 아무것도 안 떠요**: 바로가기는 콘솔 창 없이 실행되어 오류가 보이지 않습니다.
  PowerShell에서 `.\.venv\Scripts\Activate.ps1` → `python main.py`로 실행해 오류 메시지를 확인하세요.
  이미 실행 중이면 트레이의 **Q** 아이콘을 클릭해 보세요.
- **다른 모델을 쓰고 싶어요**: `.env`에 `GEMINI_MODEL=모델이름`을 추가합니다.

---

## 6. 파일 구조

```
quiz_helper/
  main.py            # 진입점: 컨트롤 창, 히스토리 탭, 트레이, 전역 단축키
  selector.py        # 반투명 오버레이 + 드래그 영역 선택 (모니터별, DPI 배율 반영)
  capture.py         # mss 화면 캡처, PNG 변환
  gemini_client.py   # Gemini 호출, JSON 파싱, 1회 재시도, 오류 분류
  result_card.py     # 우측 하단 결과 카드
  history.py         # 최근 10개 결과를 JSON으로 저장
  create_shortcut.bat  # 더블클릭 → 바탕화면 바로가기 생성 (create_shortcut.ps1 호출)
  create_shortcut.ps1
  icon.ico           # 앱/바로가기 아이콘
  requirements.txt
  .env.example
  README.md
```
