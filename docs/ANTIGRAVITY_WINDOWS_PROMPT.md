# 안티그래비티에 붙여 넣을 프롬프트

```text
내 약국순익 프로젝트를 Windows 데스크톱에서 개인용으로 쓰도록 이전해줘.
GitHub gokuroku720818/pharmacy-profit의 PR #21 브랜치 codex/pool-recovery-analysis-query를 체크아웃하고 docs/windows_local.md를 먼저 읽어줘. main에는 아직 로컬 이전 준비가 없을 수 있어.

Windows Python 3.12와 PostgreSQL 18 클라이언트 도구를 확인하고 windows/prepare-local.ps1을 실행해줘. Render 서비스의 DATABASE_URL 호스트는 Neon의 -pooler 호스트다. Render의 무료 db는 운영 장부가 아니다. Neon Console → Connect에서 직접 연결 주소를 내가 직접 안전하게 입력하게 안내해줘. 비밀번호·DB URL·장부 파일을 채팅, 로그, GitHub, 명령 인자, 스크린샷에 노출하지 마. 파일을 가져오거나 전송하는 행동은 내가 확인할 수 있게 해줘.

windows/backup-neon.ps1로 운영 PostgreSQL을 먼저 보관하고 백업 아카이브를 읽을 수 있는지 확인해줘. 가능하면 별도 빈 PostgreSQL 18 DB에 복원하여 건수와 합계를 검증해줘. 그런 뒤 windows/import-neon.ps1을 실행하여 여섯 테이블을 로컬 SQLite에 복사하고 출력된 건수·합계를 확인해줘. 예상하지 못한 테이블에 데이터가 있거나 검증에 실패하면 이전 스크립트를 무턱대고 수정하거나 기존 DB를 덮어쓰지 말고 원인을 설명해줘.

windows/start-local.ps1로 127.0.0.1:8765에 실행한 다음 기존 계정으로 로그인해서 하루 기록·메모·월 합계·잡이익·영업 일정·계산기·보고서를 원본 사이트와 대조해줘. 서비스가 시작되지 않으면 에러의 비밀 정보를 가리고 문제를 고쳐줘. 로컬 장부 파일을 Git에 커밋하지 말고, 앱 종료 후 별도 디스크에 백업하는 방법도 마련해줘.

Render 서비스 및 Neon 운영 DB는 삭제·정지·변경하지 마. 실제 데이터가 모두 옮겨졌다고 검증될 때까지 운영 DB를 유지해줘. 완료 시 무엇이 검증됐는지, 남은 데이터 차이와 위험이 무엇인지 보고해줘.
```
