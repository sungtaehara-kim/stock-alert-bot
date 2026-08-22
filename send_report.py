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
