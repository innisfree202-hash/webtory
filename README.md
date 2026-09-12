# K-Equity Signal Desk

국내 주식 이동평균 돌파 감시 Streamlit 앱 (KOSPI / KOSDAQ / KONEX)

로컬 실행: http://localhost:8501/

## 실행 방법

```bash
pip install -r requirements.txt
streamlit run app.py
```

## 환경변수 (.env, 로컬용)

```
KIS_APP_KEY=...
KIS_APP_SECRET=...
KIS_ACCESS_TOKEN=...  # 선택
```

`.env` 파일은 GitHub에 올라가지 않습니다 (`.gitignore` 처리됨).

## Streamlit Community Cloud 배포 (public URL 만들기)

1. 이 폴더를 GitHub 저장소에 push
2. https://share.streamlit.io/ 접속 → New app → 저장소/브랜치/main file `app.py` 선택 → Deploy
3. 공개 URL 예: `https://<app-name>.streamlit.app`
4. KIS 실시간 호가가 필요하면 Streamlit Cloud → App settings → Secrets 에 등록:

```toml
KIS_APP_KEY = "..."
KIS_APP_SECRET = "..."
KIS_ACCESS_TOKEN = "..."
```

앱 코드는 `st.secrets` → `os.environ` 자동 연동을 지원하므로 별도 수정 없이 동작합니다.

## 주의

- `Input/holdings.json` 은 개인 보유정보이므로 GitHub에 올라가지 않습니다.
- `FinanceDataReader` 기반 공개 일별 데이터로 후보를 선별하고, 장중에만 KIS API로 호가를 조회합니다.