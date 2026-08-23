import argparse
import json
import os
from datetime import datetime, timedelta
from io import StringIO
from pathlib import Path

import FinanceDataReader as fdr
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import mplfinance as mpf
import pandas as pd
import requests
from bs4 import BeautifulSoup

WATCHLIST = [("삼성전자", "005930"), ("SK하이닉스", "000660")]
WATCHLIST_EN = [("Samsung Electronics", "005930"), ("SK Hynix", "000660")]
REFERENCE = [("원/달러 환율", "USD/KRW", "원"), ("WTI 유가", "CL=F", "$")]

NAVER_HEADERS = {"User-Agent": "Mozilla/5.0"}

REPO = "sungtaehara-kim/stock-alert-bot"
BRANCH = "main"
CHARTS_DIR = Path("charts")
MESSAGE_FILE = Path("report_message.txt")
CHART_PATH_FILE = Path("report_chart_path.txt")


def last_two_closes(code: str):
    df = fdr.DataReader(code, (datetime.now() - timedelta(days=15)).strftime("%Y-%m-%d"))
    return float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2])


def technical_signals(code: str):
    """RSI/MACD/이동평균 크로스 등 객관적 기술적 신호를 계산해서 해석 문자열 리스트로 반환."""
    df = fdr.DataReader(code, (datetime.now() - timedelta(days=300)).strftime("%Y-%m-%d"))

    df["MA5"] = df["Close"].rolling(5).mean()
    df["MA20"] = df["Close"].rolling(20).mean()

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

    ma5, close = latest["MA5"], latest["Close"]
    prev_ma5, prev_close = prev["MA5"], prev["Close"]
    if pd.notna(ma5) and pd.notna(prev_ma5):
        if prev_close <= prev_ma5 and close > ma5:
            signals.append("5일선 상향돌파")
        elif prev_close >= prev_ma5 and close < ma5:
            signals.append("5일선 하향돌파")
        elif close > ma5:
            signals.append("5일선 위")
        else:
            signals.append("5일선 아래")

    ma20 = latest["MA20"]
    prev_ma20 = prev["MA20"]
    if pd.notna(ma20) and pd.notna(prev_ma20):
        if prev_close <= prev_ma20 and close > ma20:
            signals.append("20일선 상향돌파")
        elif prev_close >= prev_ma20 and close < ma20:
            signals.append("20일선 하향돌파")
        elif close > ma20:
            signals.append("20일선 위")
        else:
            signals.append("20일선 아래")

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


def foreign_institution_net_trading(code: str, days: int = 3):
    """최근 N거래일(전일 포함)의 기관/외국인 순매매 수량(주)을 네이버 금융에서 조회."""
    url = f"https://finance.naver.com/item/frgn.naver?code={code}"
    resp = requests.get(url, headers=NAVER_HEADERS, timeout=10)
    resp.encoding = "euc-kr"
    tables = pd.read_html(StringIO(resp.text))
    df = tables[3].dropna(how="all")
    rows = []
    for _, row in df.head(days).iterrows():
        rows.append((row.iloc[0], float(row.iloc[5]), float(row.iloc[6])))
    return rows


def foreign_top_net_buy(n: int = 5):
    """당일 거래소 전체에서 외국인 순매수 상위 종목을 네이버 금융에서 조회 (로그인 불필요, 특정 종목 추천이 아닌 실제 수급 랭킹)."""
    url = "https://finance.naver.com/sise/"
    resp = requests.get(url, headers=NAVER_HEADERS, timeout=10)
    resp.encoding = "euc-kr"
    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", id="frgn_deal_tab_1")

    rows = []
    for tr in table.find_all("tr"):
        a = tr.find("a")
        if not a:
            continue
        name = a.text.strip()
        code = a["href"].split("code=")[1]
        tds = tr.find_all("td")
        price = tds[2].get_text(strip=True)
        change_td = tds[3]
        direction = "+" if "rate_up" in change_td.get("class", []) else "-"
        change = change_td.get_text(strip=True).lstrip("상승하락")
        rows.append((name, code, price, direction, change))
        if len(rows) >= n:
            break
    return rows


def build_message() -> str:
    lines = []
    today = datetime.now().strftime("%Y-%m-%d (%a)")
    lines.append(f"\U0001F4C8 {today} 아침 시황")
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
    lines.append("[외국인/기관 순매매] (최근 3거래일, 주)")
    for name, code in WATCHLIST:
        try:
            rows = foreign_institution_net_trading(code)
            lines.append(f"{name}:")
            for date, inst, forgn in rows:
                lines.append(f"  {date} 기관 {inst:+,.0f} / 외국인 {forgn:+,.0f}")
        except Exception:
            lines.append(f"{name}: 조회 실패")

    lines.append("")
    lines.append("[외국인 순매수 상위 5종목] (당일 기준, 특정 추천 아닌 수급 랭킹)")
    try:
        for i, (name, code, price, direction, change) in enumerate(foreign_top_net_buy(), start=1):
            sign = "▲" if direction == "+" else "▼"
            lines.append(f"{i}. {name} ({code}): {price}원 ({sign}{change})")
    except Exception:
        lines.append("조회 실패")

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


def build_chart() -> Path:
    """관심종목 캔들차트(+MA5/20/60)를 하나의 이미지로 만들어 저장. (영문 라벨만 사용 - 폰트 설치 불필요)"""
    CHARTS_DIR.mkdir(exist_ok=True)
    today_str = datetime.now().strftime("%Y%m%d")
    out_path = CHARTS_DIR / f"{today_str}.png"

    fig, axes = plt.subplots(len(WATCHLIST_EN), 1, figsize=(9, 4.5 * len(WATCHLIST_EN)))
    if len(WATCHLIST_EN) == 1:
        axes = [axes]

    for ax, (name_en, code) in zip(axes, WATCHLIST_EN):
        df = fdr.DataReader(code, (datetime.now() - timedelta(days=150)).strftime("%Y-%m-%d"))
        df["MA5"] = df["Close"].rolling(5).mean()
        df["MA20"] = df["Close"].rolling(20).mean()
        plot_df = df.tail(90)

        mpf.plot(
            plot_df,
            type="candle",
            style="yahoo",
            ax=ax,
            volume=False,
            addplot=[
                mpf.make_addplot(plot_df["MA5"], ax=ax, width=0.8),
                mpf.make_addplot(plot_df["MA20"], ax=ax, width=0.8),
            ],
        )
        ax.set_title(f"{name_en} ({code})")

    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close(fig)
    return out_path


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


def send_kakao_feed(access_token: str, image_url: str) -> None:
    payload = {
        "object_type": "feed",
        "content": {
            "title": "오늘의 관심종목 차트",
            "description": datetime.now().strftime("%Y-%m-%d") + " 캔들차트 (5/20일선)",
            "image_url": image_url,
            "image_width": 1000,
            "image_height": 1000,
            "link": {"web_url": "https://finance.naver.com", "mobile_web_url": "https://finance.naver.com"},
        },
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
        raise RuntimeError(f"카카오 이미지 전송 실패: {result}")


def prepare():
    text = build_message()
    print(text)
    MESSAGE_FILE.write_text(text, encoding="utf-8")

    chart_path = build_chart()
    CHART_PATH_FILE.write_text(str(chart_path.as_posix()), encoding="utf-8")
    print(f"차트 저장: {chart_path}")


def send():
    text = MESSAGE_FILE.read_text(encoding="utf-8")
    token = refresh_access_token()
    send_kakao_memo(token, text)
    print("텍스트 전송 완료")

    if CHART_PATH_FILE.exists():
        chart_rel_path = CHART_PATH_FILE.read_text(encoding="utf-8").strip()
        image_url = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}/{chart_rel_path}"
        send_kakao_feed(token, image_url)
        print("차트 이미지 전송 완료")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["prepare", "send", "all"], nargs="?", default="all")
    args = parser.parse_args()

    if args.mode == "prepare":
        prepare()
    elif args.mode == "send":
        send()
    else:
        prepare()
        send()
