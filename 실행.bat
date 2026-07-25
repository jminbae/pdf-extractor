@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0pubnexus"

rem 파이썬 위치: 이 PC에 만들어 둔 전용 가상환경
set PY=C:\Users\배정민\pnxvenv\Scripts\python.exe
if not exist "%PY%" (
  echo [오류] 파이썬을 찾을 수 없습니다: %PY%
  echo        다른 PC에서 쓰시려면 이 줄의 경로를 그 PC의 파이썬으로 바꾸세요.
  pause & exit /b 1
)

:menu
cls
echo ============================================================
echo   논문 PDF 구조화 도구
echo ============================================================
echo.
echo   1. 논문 처리      PDF 폴더를 읽어 구조화 JSON 만들기
echo   2. 색인 만들기    구조화된 논문을 검색 가능하게
echo   3. 질문하기       논문 내용에 질문
echo   4. 품질 측정      검색이 제대로 되는지 점수내기
echo   5. 현재 상태      지금까지 뭐가 처리됐는지
echo.
echo   0. 끝내기
echo.
set /p sel="번호 입력: "

if "%sel%"=="1" goto ingest
if "%sel%"=="2" goto index
if "%sel%"=="3" goto ask
if "%sel%"=="4" goto eval
if "%sel%"=="5" goto status
if "%sel%"=="0" exit /b 0
goto menu

:ingest
echo.
echo 처리할 PDF 폴더를 config.yaml 의 input_dir 에 지정해 두었습니다.
echo 중간에 멈춰도 다시 실행하면 이어서 합니다.
echo.
"%PY%" run_pilot.py --skip-grobid
echo.
echo ※ GROBID 서버가 있으면 --skip-grobid 를 빼고 돌리면 품질이 올라갑니다.
pause & goto menu

:index
echo.
"%PY%" run_rag.py
pause & goto menu

:ask
echo.
set /p q="질문: "
if "%q%"=="" goto menu
"%PY%" ask.py "%q%" -k 5
echo.
pause & goto menu

:eval
echo.
"%PY%" evaluate.py --no-rerank
pause & goto menu

:status
echo.
"%PY%" -c "import sys,json,glob;sys.path.insert(0,'src');from pubnexus import utils;w=utils.resolve(utils.load_config()['project']['work_dir']);n=len(glob.glob(str(w/'normalized'/'*.json')));c=w/'chunks.jsonl';cn=sum(1 for _ in open(c,encoding='utf-8')) if c.exists() else 0;print(f'구조화된 논문: {n}편');print(f'검색 단위(청크): {cn}개');print(f'산출물 위치: {w}')"
echo.
pause & goto menu
