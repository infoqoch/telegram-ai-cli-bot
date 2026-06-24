# Antigravity (Agy) CLI 연동 및 세션 아키텍처 메모

이 문서는 텔레그램 봇과 Antigravity(Agy) CLI 연동에 관한 논의 내용을 정리한 메모입니다.

## 1. 통신 및 응답 처리 (Communication)
- 봇은 실시간 메시지를 주고받을 때 `subprocess`를 통해 `agy` 명령어를 백그라운드에서 실행합니다.
- 명령어 예시: `agy --model "Gemini 3.1 Pro (High)" --dangerously-skip-permissions --print-timeout 30m --print "<USER_REQUEST>내용</USER_REQUEST>"`
- CLI의 결과(stdout)를 텍스트로 그대로 읽어와서 사용자에게 응답합니다. (터미널 UI 요소 제거를 위해 `--print` 옵션 활용)

## 2. 과거 세션 발견 (Session Discovery)
- 봇에서 이전 대화 목록(History)을 띄울 때는 CLI 명령어를 실행하지 않고 로컬 파일을 직접 파싱합니다.
- `~/.gemini/antigravity-cli/history.jsonl`: 세션 ID와 작업 공간(Workspace) 매핑 확인.
- `~/.gemini/antigravity-cli/brain/*/logs/transcript.jsonl`: 파일 내 `<USER_REQUEST>` 태그 안의 텍스트를 파싱하여 세션의 제목으로 사용.

## 3. 세션 ID 식별 기법 (ID Resolution)
- **문제점:** Claude(`--output-format json` 지원) 등과 달리, 현재 Agy CLI의 `--print` 모드는 순수 텍스트만 출력하므로 응답 내부에 새로 생성된 세션 ID(UUID) 정보가 포함되어 있지 않습니다.
- **해결책 (스냅샷 비교 기법):** 
  1. 명령어 실행 직전, `brain` 폴더 내 로그 파일들의 수정 시간(mtime) 스냅샷 저장 (Before)
  2. `agy --print` 명령어 실행
  3. 명령어 실행 직후, 다시 수정 시간 스냅샷 캡처 (After)
  4. Before와 After를 비교하여, 새로 생성되었거나 가장 최근에 수정 시간이 갱신된 폴더명(UUID)을 찾아내어 이를 **새로운 세션 ID**로 식별합니다. (`_resolve_created_session_id` 로직)
