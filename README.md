# PDF Extractor

Windows에서 PDF·Word·PowerPoint·Excel·한글 문서 등을 Markdown과 로컬 이미지로
자동 저장하는 문서 추출기입니다. 파일·폴더 선택과 드래그 입력, 진행 상태 표시,
원본과 Markdown 대조·검색, 저장 위치 설정과 업데이트 기능을 제공합니다.

논문에는 기존 GROBID/JATS 경로를 유지하고, 일반 PDF에는 공식 PyMuPDF Markdown
엔진을 별도 프로세스로 사용합니다. HWP/HWPX는 rHWP를 사용합니다. 로컬 OCR과
레이아웃 모델은 사용하지만 유료 LLM/API에 문서를 보내지 않습니다.

기본 결과 위치는 사용자의 실제 문서 폴더 아래 `문서 추출기`이며 설정에서 바꿀 수
있습니다. 그림은 결과 폴더의 `assets` 아래에 상대 경로로 저장됩니다. 원본은
수정하지 않습니다. 복잡한 병합 표는 Markdown 파일 안의 HTML 표로 보존합니다.
OCR·지원되지 않는 문서는 확인이 필요한 상태를 표시합니다.

RTF·구형 XLS/MSG 등은 추가 로컬 도구가 필요할 수 있습니다. Office 원본의 PDF
미리보기는 LibreOffice 설치 여부와 글꼴에 따라 달라집니다.

- [배포 파일](https://github.com/jminbae/pdf-extractor/releases/latest)
- [빌드와 소스 안내](docs/implementation/BUILDING.md)
- [추출 엔진 원칙](docs/implementation/ENGINE_POLICY.md)
- [기존 논문 파이프라인](pubnexus/README.md)
- [GNU AGPL v3 라이선스](LICENSE)
