import type { AppProps } from "next/app";
import Head from "next/head";
import "@/styles/globals.css";

export default function App({ Component, pageProps }: AppProps) {
  return (
    <>
      <Head>
        <title>산불先지도 — 어디를 먼저 지킬 것인가</title>
        <meta
          name="description"
          content="전국 500m 격자의 향후 1~3시간 산불 발화 위험을 AI로 예측하고 SGIS 인구·가구·주택 통계를 결합해 대응 우선지역을 제시합니다."
        />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </Head>
      <Component {...pageProps} />
    </>
  );
}
