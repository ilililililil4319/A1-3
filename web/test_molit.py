# test_molit.py - 6개월 데이터 + OpenAI 요약까지 테스트
import os
import requests
import xml.etree.ElementTree as ET
from dotenv import load_dotenv
from openai import OpenAI   # OpenAI 라이브러리

load_dotenv(".env.local")
SERVICE_KEY = os.environ.get("MOLIT_API_KEY")
API_URL = os.environ.get("MOLIT_API_URL")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")

LAWD_CD = "48123"  # 성산구
GU_NAME = "성산구"
months = ["202503", "202504", "202505", "202506", "202507", "202508"]


# 한 달 평균 거래가 구하기
def get_month_average(lawd_cd, deal_ymd):
    params = {
        "serviceKey": SERVICE_KEY,
        "LAWD_CD": lawd_cd,
        "DEAL_YMD": deal_ymd,
        "numOfRows": "1000",
        "pageNo": "1",
    }
    response = requests.get(API_URL, params=params, timeout=10)
    root = ET.fromstring(response.text)
    prices = []
    for item in root.iter("item"):
        amount_text = item.findtext("dealAmount")
        if amount_text:
            prices.append(int(amount_text.replace(",", "").strip()))
    if prices:
        return round(sum(prices) / len(prices)), len(prices)
    return None, 0


# 1) 6개월 데이터 모으기
monthly = []
for ym in months:
    avg, count = get_month_average(LAWD_CD, ym)
    if avg:
        monthly.append((ym, avg))
        print(f"{ym}: 평균 {avg}만원 ({count}건)")

# 2) 변동률 계산
first_avg = monthly[0][1]
last_avg = monthly[-1][1]
change = round((last_avg - first_avg) / first_avg * 100, 1)
print(f"변동률: {change}%")
print("-" * 40)

# 3) AI에게 줄 데이터를 문장으로 정리
data_text = f"{GU_NAME}의 최근 6개월 아파트 평균 매매가(만원): "
data_text += ", ".join([f"{ym[4:6]}월 {avg}" for ym, avg in monthly])
data_text += f". 첫 달 대비 마지막 달 변동률은 {change}%입니다."

# 4) OpenAI 호출
client = OpenAI(api_key=OPENAI_KEY)

prompt = f"""당신은 부동산 데이터를 일반인이 이해하기 쉽게 설명하는 애널리스트입니다.
다음은 창원시 {GU_NAME}의 최근 6개월 아파트 평균 매매가 데이터입니다.

작성 조건:
1. 데이터에 근거한 사실만 서술하고, 추측성 투자 조언은 하지 않습니다.
2. "평균 매매가가 약 O% 상승/하락했습니다" 형태로 핵심 변동을 한두 문장으로 요약합니다.
3. 전문 용어 대신 일반인이 이해할 수 있는 표현을 사용합니다.

데이터: {data_text}"""

response = client.chat.completions.create(
    model="gpt-4o-mini",   # 저렴하고 빠른 모델
    messages=[{"role": "user", "content": prompt}],
    max_tokens=200,
)

# 5) 결과 출력
print("AI 요약 결과:")
print(response.choices[0].message.content)
