# PPTX 미리보기 구성요소 고지

미리보기는 공개 배포물 `@aiden0z/pptx-renderer` 1.4.0의 브라우저 번들을 수정 없이 사용합니다. 주 라이선스는 Apache-2.0입니다. 이 디렉터리의 구성요소별 LICENSE, NOTICE 및 저작권 고지를 함께 보존하십시오. 정확한 배포물 URL, 무결성 값, 버전과 각 고지의 SHA256은 동봉 `presentation-renderer-manifest.json`에 기록됩니다.

- Apache ECharts 6.0.0: Apache-2.0. `echarts/NOTICE`와 `echarts/licenses/LICENSE-d3`도 보존합니다.
- zrender 6.0.0: BSD-3-Clause. tslib 2.3.0: 0BSD.
- JSZip 3.10.1: MIT 또는 GPL-3.0-or-later의 이중 라이선스 중 이 배포에서는 MIT 조건을 적용합니다. JSZip의 배포 LICENSE 원문을 보존합니다.
- pako 1.0.11: MIT 및 Zlib. inherits 2.0.4: ISC. 나머지 수집된 JavaScript 보조 패키지의 라이선스는 각 디렉터리에 있습니다.
- isarray 1.0.0은 별도 LICENSE 파일 대신 README에 MIT 전문과 저작권자가 있습니다. 해당 README를 원문 그대로 보존합니다.
- ECMA-376에서 기계적으로 유래한 도형 정의의 ECMA 저작권 고지는 `aiden0z-pptx-renderer/licenses/ECMA-text-copyright-notice.txt`에 있습니다. 원본 ECMA 규격 ZIP은 이 프로그램에 포함하지 않습니다.

## MPL-2.0 소스 제공

번들에는 `mtx-decompressor` 1.4.2의 수정하지 않은 코드가 포함됩니다. 이 구성요소와 그 소스는 Mozilla Public License 2.0에 따라 제공됩니다. MPL 전문은 `mtx-decompressor/LICENSE` 및 `aiden0z-pptx-renderer/licenses/mtx-decompressor-MPL-2.0.txt`에 있습니다.

대응 소스는 이 고지와 함께 배포되는 `sources/mtx-decompressor-v1.4.2-source.tar.gz`에 있습니다. 원본 소스는 [공식 v1.4.2 태그](https://github.com/ChristopherVR/mtx-decompressor/tree/v1.4.2)에서도 받을 수 있습니다. 동봉 소스 아카이브의 SHA256은 `e4edf429b1614a8f7366a55cffe58b35c58e784f0d4719d209cc3aa28ef3d970`입니다. 이 소스에 대한 수령인의 MPL 권리를 제한하지 않습니다.

고지 수집은 공식 태그의 전체 런타임 의존성 폐쇄를 보수적으로 포함합니다. 브라우저 번들에서 제거된 Node 전용 파일도 이 목록에 있을 수 있습니다. JSZip은 이미 만들어진 브라우저 하위 번들을 포함하므로 하위 번들의 개별 코드 버전을 현재 lock 값으로 단정하지 않습니다. 선택적 `pdfjs-dist`는 사용하거나 동봉하지 않습니다.
