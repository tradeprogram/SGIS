import type { AppProps } from "next/app";
import Head from "next/head";
import "@/styles/globals.css";

// og:image 는 상대경로면 대부분의 메신저가 못 읽는다. 절대경로를 만든다.
// Vercel 이 넣어 주는 VERCEL_PROJECT_PRODUCTION_URL 을 쓰고, 없으면 기본값.
const SITE = process.env.NEXT_PUBLIC_SITE_URL
  ? process.env.NEXT_PUBLIC_SITE_URL.replace(/\/$/, "")
  : "https://wildfire-predict-framework.vercel.app";

export default function App({ Component, pageProps }: AppProps) {
  return (
    <>
      <Head>
        <title>산불 발화예측·우선대응 통합지도 — 어디를 먼저 지킬 것인가</title>
        <meta
          name="description"
          content="전국 500m 격자의 향후 1~3시간 산불 발화 위험을 AI로 예측하고 SGIS 인구·가구·주택 통계를 결합해 대응 우선지역을 제시합니다."
        />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <link rel="icon" href="/logo.png" type="image/png" />
        <link rel="apple-touch-icon" href="/logo.png" />
        {/* 링크 미리보기 카드. og:image 는 절대경로여야 카카오톡·슬랙 등에서 뜬다. */}
        <meta property="og:type" content="website" />
        <meta property="og:site_name" content="산불 발화예측·우선대응 통합지도" />
        <meta property="og:title" content="산불 발화예측·우선대응 통합지도" />
        <meta
          property="og:description"
          content="SGIS 통계지리 기반 500m 격자 의사결정 시스템 — 전국 403,385격자의 향후 1~3시간 산불 발화 위험을 예측하고, 어디를 먼저 지킬지 답합니다."
        />
        <meta property="og:image" content={`${SITE}/og.png`} />
        <meta property="og:image:width" content="1200" />
        <meta property="og:image:height" content="630" />
        <meta property="og:url" content={SITE} />
        <meta name="twitter:card" content="summary_large_image" />
        <meta name="twitter:title" content="산불 발화예측·우선대응 통합지도" />
        <meta
          name="twitter:description"
          content="SGIS 통계지리 기반 500m 격자 의사결정 시스템"
        />
        <meta name="twitter:image" content={`${SITE}/og.png`} />
      </Head>
      <Component {...pageProps} />
    </>
  );
}
