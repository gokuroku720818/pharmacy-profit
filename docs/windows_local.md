# 윈도우에서 개인용으로 실행하기

이 문서는 웹사이트의 **Neon PostgreSQL** 장부를 자신의 PC에 옮기기 위한 절차입니다. Render 웹 서비스는 Neon의 `DATABASE_URL`에 연결되어 있습니다. Render 대시보드의 무료 `db`는 현재 웹 서비스의 연결 대상이 아닙니다. 모든 로컬 실행은 `127.0.0.1`에만 열립니다. **기존 서비스나 Neon DB를 끄거나 삭제하기 전에** 이전 결과를 직접 대조하세요.

## 준비

- Windows 10/11, Python 3.12 (`py -3.12`), PostgreSQL **18** 클라이언트 도구(`pg_dump`, `pg_restore`).
- 이 저장소의 최신 `codex/pool-recovery-analysis-query` 브랜치(PR #21 포함). `main`에는 아직 로컬 이전 파일이 없습니다.
- Neon Console의 프로젝트 → **Connect** → 직접 연결 주소(`-pooler`가 없는 호스트). Render 서비스의 주소는 pooler 호스트이므로 직접 연결 주소를 사용하세요. 주소에는 비밀번호가 포함되어 있습니다. 채팅, 저장소, 스크린샷, PowerShell 명령 인자로 남기지 마세요.
- PowerShell에서 프로젝트 루트로 이동해 아래 명령을 실행합니다. 실행 정책 때문에 스크립트를 차단하면 현재 세션에서만 `Set-ExecutionPolicy -Scope Process Bypass`를 사용할 수 있습니다.

```powershell
.\windows\prepare-local.ps1
.\windows\backup-neon.ps1
```

백업은 `data/neon-YYYYMMDD-HHMMSS.dump`에 저장됩니다. 스크립트는 `pg_restore --list`로 아카이브를 읽을 수 있는지 확인합니다. **이는 실제 복원 확인을 대신하지 않습니다.** 가능하면 별도의 빈 PostgreSQL 18 데이터베이스를 만들어 `pg_restore`로 복원하고 계정·일별 장부·잡이익 건수와 월합계를 확인하세요. 백업 파일을 PC 외부의 보호된 저장소에도 한 벌 보관하세요.

## 기존 장부를 SQLite로 이전

```powershell
.\windows\import-neon.ps1
.\windows\start-local.ps1
```

두 스크립트는 접속 주소를 숨김 입력으로 받습니다. 이전 도구는 PostgreSQL을 읽기 전용 일관된 시점에서 조회합니다. 회원, 일별 장부, 월 요약, 계산기 설정, 영업 일정, 잡이익을 **새로운** `data/sales.db`에 복사한 다음 테이블별 건수·주요 합계·외래키·SQLite 무결성을 점검합니다. 예상 밖의 데이터가 있는 테이블이나 이미 존재하는 `sales.db`를 만나면 중단합니다. 기존 파일을 덮어쓰지 않습니다. 비밀번호와 주소를 출력하지 않습니다.

브라우저에서 `http://127.0.0.1:8765/login`을 열고 **기존 사이트 관리자 계정**으로 로그인하세요. 날짜별 메모, 월 합계, 잡이익, 영업 일정, 계산기 설정, 보고서·내보내기까지 실제 운영 화면과 대조하세요. 새 개인 장부를 만들 경우에는 이전 스크립트를 사용하지 않고 `start-local.ps1`을 처음 실행해 **새 관리자 비밀번호**를 설정합니다.

## 사용 및 백업

- 사용할 때마다 `windows/start-local.ps1`을 실행하고, 마칠 때 PowerShell 창에서 `Ctrl+C`를 누릅니다. PC가 꺼지면 접속할 수 없습니다.
- `data/sales.db`와 `data/local_secret.key`는 Git에 올리지 마세요. 정기적으로 앱을 종료한 후 `sales.db`를 다른 디스크에 복사하세요. 실행 중 백업은 SQLite 온라인 백업 API를 사용해야 일관성이 보장됩니다.
- `local_secret.key`가 바뀌면 모든 세션에서 로그아웃됩니다. 장부 자체는 이 파일로 암호화되지 않으므로 PC의 사용자 계정과 디스크 암호화를 유지하세요.
- Render 대시보드에 표시된 무료 DB 만료일은 현재 Neon 장부의 만료일이 아닙니다. 이전 검증 후 Render 종료 여부는 별도로 결정하세요.

## 중단 시

- `import-neon.ps1` 실패 시 원본 DB와 기존 `data/sales.db`는 변하지 않습니다. 오류를 해결한 후 재시도하세요. 민감한 접속 주소가 포함될 수 있는 원본 오류 로그는 공유하지 마세요.
- 로컬 로그인 실패 시 서버 실행 창의 오류 유형만 확인하고, 암호나 DB URL은 전달하지 마세요. `SECRET_KEY`는 `prepare-local.ps1`이 만들어 둔 파일에서 읽습니다.
- 알 수 없는 원본 테이블에 데이터가 있다면 이전 도구가 안전하게 중단합니다. 해당 테이블을 확인한 뒤 이전 대상에 포함할지 결정해야 합니다.
