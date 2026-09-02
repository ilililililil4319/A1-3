# api/analyze.py - 창원댁 AI 시장분석 백엔드 (Vercel Serverless Function)
# 프론트에서 fetch('/api/analyze?gu=성산구') 로 호출하면
# 국토부 6개월 데이터 -> 평균/변동률 계산 -> OpenAI 요약 -> JSON 응답

import os
import json
import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from openai import OpenAI

# 창원 5개 구의 지역코드(LAWD_CD)
GU_CODES = {
    "의창구": "48121",
    "성산구": "48123",
    "마산합포구": "48125",
    "마산회원구": "48127",
    "진해구": "48129",
}

# ------------------------------------------------------------------
# [사전평가 #4 보완] 입력 검증 설정
# gu 파라미터는 프론트에서는 <select> 드롭다운으로 제한되지만,
# API는 누구나 쿼리스트링으로 직접 호출할 수 있으므로 백엔드에서도
# 반드시 길이·형식·화이트리스트 검증을 해야 한다.
# ------------------------------------------------------------------
MAX_GU_LENGTH = 10  # "마산합포구"(5자) 기준으로 여유 있게 설정한 상한

# ------------------------------------------------------------------
# [사전평가 #16 보완] 초경량 응답 캐시
# Vercel Python 서버리스 함수는 요청마다 새로 실행되는 것이 원칙이지만,
# 동일 컨테이너가 "warm" 상태로 재사용되는 짧은 시간 동안에는
# 모듈 레벨 전역 변수가 메모리에 유지된다. 이 특성을 이용해
# 같은 구를 짧은 시간 내에 다시 조회할 때 국토부 API + OpenAI API를
# 다시 호출하지 않고 캐시된 결과를 즉시 반환해 응답 지연을 줄인다.
#
# 주의: 이 캐시는 "best-effort" 최적화이며, 인스턴스가 새로 뜨거나(cold start)
# 여러 인스턴스에 요청이 분산되면 공유되지 않는다. 완전히 신뢰 가능한
# 캐시가 필요하다면 Vercel KV(Redis) 같은 외부 저장소를 사용해야 한다.
# (자세한 대안은 README "성능/응답 지연 개선 방안" 참고)
# ------------------------------------------------------------------
CACHE = {}
CACHE_TTL_SECONDS = 600  # 10분


def validate_gu(raw_gu):
    """
    사용자 입력값(gu)을 검증한다. (#4 보완)
    - None/빈 값인지
    - 비정상적으로 긴 입력인지 (오용·잘못된 호출 방지)
    - 창원 5개 구 화이트리스트에 정확히 포함되는지

    반환값: (정상화된 gu 문자열, None) 또는 (None, 에러 메시지)
    """
    if raw_gu is None:
        return None, "구를 선택해주세요."

    gu = raw_gu.strip()

    if gu == "":
        return None, "구를 선택해주세요."

    if len(gu) > MAX_GU_LENGTH:
        return None, "입력값이 너무 깁니다. 목록에서 구를 선택해주세요."

    if gu not in GU_CODES:
        return None, "지원하지 않는 지역입니다. 창원시 5개 구 중에서 선택해주세요."

    return gu, None


def get_cached(gu):
    """캐시에서 유효한(만료되지 않은) 결과를 가져온다."""
    entry = CACHE.get(gu)
    if not entry:
        return None
    cached_at, data = entry
    if time.time() - cached_at > CACHE_TTL_SECONDS:
        CACHE.pop(gu, None)  # 만료된 캐시는 정리
        return None
    return data


def set_cache(gu, data):
    """결과를 캐시에 저장한다."""
    CACHE[gu] = (time.time(), data)


# 최근 6개월의 YYYYMM 목록 만들기 (신고 지연 고려해 2개월 전부터 거슬러 6개)
def get_recent_months():
    now = datetime.now()
    # 기준: 2개월 전 (최근 1~2개월은 신고 지연으로 데이터가 적을 수 있음)
    base_year = now.year
    base_month = now.month - 2
    while base_month <= 0:
        base_month += 12
        base_year -= 1

    months = []
    y, m = base_year, base_month
    for _ in range(6):
        months.append(f"{y}{m:02d}")
        m -= 1
        if m <= 0:
            m += 12
            y -= 1
    months.reverse()  # 오래된 달 -> 최근 달 순서
    return months


# 한 달 평균 거래가 구하기 (여러 페이지 합산)
def get_month_average(lawd_cd, deal_ymd, service_key, api_url):
    prices = []
    try:
        params = {
            "serviceKey": service_key,
            "LAWD_CD": lawd_cd,
            "DEAL_YMD": deal_ymd,
            "numOfRows": "1000",
            "pageNo": "1",
        }
        res = requests.get(api_url, params=params, timeout=8)
        root = ET.fromstring(res.text)
        for item in root.iter("item"):
            amt = item.findtext("dealAmount")
            if amt and amt.strip():
                try:
                    prices.append(int(amt.replace(",", "").strip()))
                except ValueError:
                    continue
    except Exception:
        return None

    if prices:
        return round(sum(prices) / len(prices))
    return None


# 실제 분석 로직
def analyze_gu(gu_name):
    # [#16 보완] 캐시 확인 - 짧은 시간 내 같은 구 재조회는 즉시 반환
    cached = get_cached(gu_name)
    if cached:
        result = dict(cached)
        result["cached"] = True
        return result

    service_key = os.environ.get("MOLIT_API_KEY")
    api_url = os.environ.get("MOLIT_API_URL")
    openai_key = os.environ.get("OPENAI_API_KEY")

    # 환경변수 누락 방어
    if not service_key or not api_url:
        return {"error": "서버 설정 오류(국토부 API 키)가 확인되지 않습니다."}
    if not openai_key:
        return {"error": "서버 설정 오류(OpenAI 키)가 확인되지 않습니다."}

    lawd_cd = GU_CODES.get(gu_name)
    if not lawd_cd:
        return {"error": "지원하지 않는 지역입니다."}

    # 6개월 데이터 수집
    months = get_recent_months()
    monthly = []
    for ym in months:
        avg = get_month_average(lawd_cd, ym, service_key, api_url)
        if avg:
            monthly.append({"month": ym, "avg": avg})

    if len(monthly) < 2:
        return {"error": "해당 기간에 분석할 거래 데이터가 충분하지 않습니다."}

    # 변동률 계산
    first_avg = monthly[0]["avg"]
    last_avg = monthly[-1]["avg"]
    change = round((last_avg - first_avg) / first_avg * 100, 1)

    # AI에게 줄 데이터 문장 (단위: 만원 명시)
    data_text = f"{gu_name}의 최근 6개월 아파트 평균 매매가(단위: 만원): "
    data_text += ", ".join([f"{m['month'][4:6]}월 {m['avg']}만원" for m in monthly])
    data_text += f". 첫 달 대비 마지막 달 변동률은 {change}%입니다."

    # OpenAI 요약
    try:
        client = OpenAI(api_key=openai_key)
        prompt = f"""당신은 부동산 데이터를 일반인이 이해하기 쉽게 설명하는 애널리스트입니다.
다음은 창원시 {gu_name}의 최근 6개월 아파트 평균 매매가 데이터입니다.

작성 조건:
1. 데이터에 근거한 사실만 서술하고, 추측성 투자 조언은 하지 않습니다.
2. "평균 매매가가 약 {change}% 상승/하락했습니다" 형태로 핵심 변동을 먼저 서술합니다.
3. 금액은 '만원' 단위 그대로 쓰되, 억 단위로 환산해 함께 표기해도 됩니다. (예: 38050만원 = 약 3억 8천만원)
4. 전문 용어 대신 일반인이 이해할 수 있는 표현으로 2~3문장으로 요약합니다.

데이터: {data_text}"""

        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=250,
        )
        summary = completion.choices[0].message.content.strip()
    except Exception:
        return {"error": "AI 요약 생성 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."}

    result = {
        "gu": gu_name,
        "change": change,
        "monthly": monthly,
        "summary": summary,
        "cached": False,
    }
    set_cache(gu_name, result)  # [#16 보완] 다음 요청을 위해 캐시에 저장
    return result


# ============ Vercel이 호출하는 진입점(handler) ============
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        query = parse_qs(urlparse(self.path).query)
        raw_gu = query.get("gu", [None])[0]

        # [#4 보완] 빈 입력뿐 아니라 비정상적으로 긴 입력, 목록에 없는 값도 여기서 차단
        gu, error = validate_gu(raw_gu)
        if error:
            self._send(400, {"error": error})
            return

        result = analyze_gu(gu)
        status = 200 if "error" not in result else 500
        self._send(status, result)

    def _send(self, status, data):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
