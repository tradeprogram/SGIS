import { Html, Head, Main, NextScript } from "next/document";

export default function Document() {
  return (
    <Html lang="ko">
      <Head>
        {/* maplibre-gl은 번들에 넣지 않고 CDN에서 로드한다.
            Next dev(pages router)에서 이 라이브러리의 async 청크가 컴파일되지 않아
            하이드레이션이 영구히 멈추는 문제가 있었다. */}
        <link
          rel="stylesheet"
          href="https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css"
        />
      </Head>
      <body>
        <Main />
        <NextScript />
      </body>
    </Html>
  );
}
