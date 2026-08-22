import json
import os
from datetime import datetime, timedelta
from io import StringIO

import FinanceDataReader as fdr
import pandas as pd
import requests

INDICES = [("KOSPI", "KS11"), ("KOSDAQ", "KQ11"), ("나스닥", "IXIC"), ("S&P500", "US500")]
WATCHLIST = [("삼성전자", "005930"), ("SK하이닉스", "000660")]
REFERENCE = [("원/달러 환율", "USD/KRW", "원"), ("WTI 유가", "CL=F", "$")]

NAVER_HEADERS = {"User-Agent": "Mozilla/5.0"}


def last_two_closes(code: str):
    df = fdr.DataReader(code, (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d"))
    return float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2])


def technical_signals(code: str):
    """RSI/MACD/이동평균 크로스 등 객관적 기술적 신호를 계산해서 해석 문자열 리스트로 반환."""
    df = fdr.DataReader(code, (datetime.now() - timedelta(days=300)).strftime("%Y-%m-%d"))

    df["MA20"] = df["Close"].rolling(20).mean()
    df["MA60"] = df["Close"].rolling(60).mean()

    delta = df["Close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14).mean()
    rs = avg_gain / avg_loss
    df["RSI"] = 100 - (100 / (1 + rs))

    ema_fast = df["Close"].ewm(span=12, adjust=False).mean()
    ema_slow = df["Close"].ewm(span=26, adjust=False).mean()
    df["MACD"] = ema_fast - ema_slow
    df["MACD_SIGNAL"] = df["MACD"].ewm(span=9, adjust=False).mean()

    latest, prev = df.iloc[-1], df.iloc[-2]
    signals = []

    ma20, ma60 = latest["MA20"], latest["MA60"]
    prev_ma20, prev_ma60 = prev["MA20"], prev["MA60"]
    if pd.notna(ma20) and pd.notna(ma60) and pd.notna(prev_ma20) and pd.notna(prev_ma60):
        if prev_ma20 <= prev_ma60 and ma20 > ma60:
            signals.append("골든크로스 발생")
        elif prev_ma20 >= prev_ma60 and ma20 < ma60:
            signals.append("데드크로스 발생")
        elif ma20 > ma60:
            signals.append("20일선>60일선 (상승추세)")
        else:
            signals.append("20일선<60일선 (하락추세)")

    rsi = latest["RSI"]
    if pd.notna(rsi):
        if rsi >= 70:
            signals.append(f"RSI {rsi:.0f} (과매수)")
        elif rsi <= 30:
            signals.append(f"RSI {rsi:.0f} (과매도)")
        else:
            signals.append(f"RSI {rsi:.0f} (중립)")

    macd, macd_signal = latest["MACD"], latest["MACD_SIGNAL"]
    prev_macd, prev_signal = prev["MACD"], prev["MACD_SIGNAL"]
    if pd.notna(macd) and pd.notna(macd_signal):
        if prev_macd <= prev_signal and macd > macd_signal:
            signals.append("MACD 상향돌파")
        elif prev_macd >= prev_signal and macd < macd_signal:
            signals.append("MACD 하향돌파")

    return signals


def foreign_institution_net_trading(code: str):
    """가장 최근 거래일의 기관/외국인 순매매 수량(주)을 네이버 금융에서 조회."""
    url = f"https://finance.naver.com/item/frgn.naver?code={code}"
    resp = requests.get(url, headers=NAVER_HEADERS, timeout=10)
    resp.encoding = "euc-kr"
    tables = pd.read_html(StringIO(resp.text))
    df = tables[3].dropna(how="all")
    latest = df.iloc[0]
    date = latest.iloc[0]
    institution_net = latest.iloc[5]
    foreign_net = latest.iloc[6]
    return date, float(institution_net), float(foreign_net)


def build_message() -> str:
    lines = []
    today = datetime.now().strftime("%Y-%m-%d (%a)")
    lines.append(f"\U0001F4C8 {today} 아침 시황")
    lines.append("")
    lines.append("[시장 지수]")
    for name, code in INDICES:
        try:
            cur, prev = last_two_closes(code)
            chg = (cur - prev) / prev * 100
            lines.append(f"{name}: {cur:,.2f} ({chg:+.2f}%)")
        except Exception:
            lines.append(f"{name}: 조회 실패")

    lines.append("")
    lines.append("[관심종목]")
    for name, code in WATCHLIST:
        try:
            cur, prev = last_two_closes(code)
            chg = (cur - prev) / prev * 100
            lines.append(f"{name}: {cur:,.0f}원 ({chg:+.2f}%)")
        except Exception:
            lines.append(f"{name}: 조회 실패")

    lines.append("")
    lines.append("[기술적 신호] (참고용, 매매 판단은 직접 해주세요)")
    for name, code in WATCHLIST:
        try:
            signals = technical_signals(code)
            lines.append(f"{name}: {', '.join(signals) if signals else '특이 신호 없음'}")
        except Exception:
            lines.append(f"{name}: 조회 실패")

    lines.append("")
    lines.append("[외국인/기관 순매매] (전일, 주)")
    for name, code in WATCHLIST:
        try:
            date, inst, forgn = foreign_institution_net_trading(code)
            lines.append(f"{name} ({date}): 기관 {inst:+,.0f} / 외국인 {forgn:+,.0f}")
        except Exception:
            lines.append(f"{name}: 조회 실패")

    lines.append("")
    lines.append("[참고 지표] (매매 판단은 직접 해주세요)")
    for name, code, unit in REFERENCE:
        try:
            cur, prev = last_two_closes(code)
            chg = (cur - prev) / prev * 100
            lines.append(f"{name}: {cur:,.2f}{unit} ({chg:+.2f}%)")
        except Exception:
            lines.append(f"{name}: 조회 실패")

    return "\n".join(lines)


def refresh_access_token() -> str:
    resp = requests.post(
        "https://kauth.kakao.com/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": os.environ["KAKAO_CLIENT_ID"],
            "client_secret": os.environ["KAKAO_CLIENT_SECRET"],
            "refresh_token": os.environ["KAKAO_REFRESH_TOKEN"],
        },
        timeout=15,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def send_kakao_memo(access_token: str, text: str) -> None:
    payload = {
        "object_type": "text",
        "text": text,
        "link": {"web_url": "https://finance.naver.com", "mobile_web_url": "https://finance.naver.com"},
    }
    resp = requests.post(
        "https://kapi.kakao.com/v2/api/talk/memo/default/send",
        headers={"Authorization": f"Bearer {access_token}"},
        data={"template_object": json.dumps(payload, ensure_ascii=False)},
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json()
    if result.get("result_code") != 0:
        raise RuntimeError(f"카카오 전송 실패: {result}")


def main():
    text = build_message()
    print(text)
    token = refresh_access_token()
    send_kakao_memo(token, text)
    print("전송 완료")


if __name__ == "__main__":
    main()
