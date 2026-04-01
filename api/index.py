"""
네이버 플레이스 진단 서비스 - Vercel Serverless + 로컬 Flask 겸용
"""

import json
import os
import re
from datetime import datetime, timezone
import requests as http_requests
from flask import Flask, render_template, request, jsonify

# Flask 템플릿 경로를 프로젝트 루트 기준으로 설정
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__, template_folder=os.path.join(ROOT_DIR, 'templates'))

# ============================================================
# Supabase 연동 (REST API 직접 호출 - 경량)
# ============================================================

SUPABASE_URL = os.environ.get('SUPABASE_URL', '')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY', '')


def _sb_headers():
    return {
        'apikey': SUPABASE_KEY,
        'Authorization': f'Bearer {SUPABASE_KEY}',
        'Content-Type': 'application/json',
        'Prefer': 'return=minimal',
    }


def _sb_available():
    return bool(SUPABASE_URL and SUPABASE_KEY)


_last_log_error = None


def log_analysis(place_id, url, name, category, grade, percentage, photo_count,
                 menu_count, review_count, review_rating):
    global _last_log_error
    if not _sb_available():
        _last_log_error = 'sb_not_available'
        return
    try:
        resp = http_requests.post(
            f'{SUPABASE_URL}/rest/v1/analyses',
            headers=_sb_headers(),
            json={
                'place_id': str(place_id),
                'url': url[:500],
                'name': name[:200],
                'category': category[:100] if category else '',
                'grade': grade,
                'percentage': percentage,
                'photo_count': photo_count,
                'menu_count': menu_count,
                'review_count': review_count,
                'review_rating': review_rating,
            },
            timeout=5,
        )
        if resp.status_code >= 400:
            _last_log_error = f'status={resp.status_code} body={resp.text[:200]}'
        else:
            _last_log_error = None
    except Exception as e:
        _last_log_error = str(e)


def _sb_select(params=''):
    if not _sb_available():
        return []
    try:
        headers = _sb_headers()
        headers['Prefer'] = 'count=exact'
        resp = http_requests.get(
            f'{SUPABASE_URL}/rest/v1/analyses?{params}',
            headers=headers,
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return []

# ============================================================
# URL 파싱
# ============================================================

def extract_place_id(url: str) -> str | None:
    url = url.strip()

    if url.isdigit():
        return url

    # 단축 URL
    if 'naver.me' in url or 'me2.do' in url:
        try:
            resp = http_requests.get(url, allow_redirects=True, timeout=5,
                                     headers={'User-Agent': 'Mozilla/5.0'})
            url = resp.url
        except Exception:
            return None

    # appLink URL에서 id 파라미터 추출
    if 'appLink' in url or 'pinId' in url:
        m = re.search(r'[?&](?:pinId|id)=(\d+)', url)
        if m:
            return m.group(1)

    for pattern in [
        r'/place/(\d+)', r'/restaurant/(\d+)', r'/cafe/(\d+)',
        r'/hairshop/(\d+)', r'/hospital/(\d+)', r'/accommodation/(\d+)',
        r'/beauty/(\d+)', r'/shopping/(\d+)', r'/food/(\d+)',
    ]:
        m = re.search(pattern, url)
        if m:
            return m.group(1)

    return None


# ============================================================
# GraphQL API 데이터 수집
# ============================================================

GRAPHQL_URL = 'https://api.place.naver.com/graphql'

GRAPHQL_QUERY = """query {
  placeDetail(input: {id: "%s"}) {
    base {
      name category address roadAddress phone id siteId
    }
    newBusinessHours {
      name
      businessHours {
        day description
        businessHours { start end }
        breakHours { start end }
      }
    }
    description
    menus { name price description }
    images { totalImages }
    visitorReviews { total }
    visitorReviewStats { review { avgRating } }
    fsasReviews { total }
    naverBooking { naverBookingUrl }
    homepages { repr { url } }
    naverOrder { items { id } }
    keywords
  }
}"""


def graphql_headers(place_id: str) -> dict:
    return {
        'User-Agent': (
            'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
            'AppleWebKit/605.1.15 (KHTML, like Gecko) '
            'Version/17.0 Mobile/15E148 Safari/604.1'
        ),
        'Content-Type': 'application/json',
        'Referer': f'https://m.place.naver.com/restaurant/{place_id}/home',
        'Origin': 'https://m.place.naver.com',
        'Accept-Language': 'ko-KR,ko;q=0.9',
    }


def fetch_place_data(place_id: str) -> dict | None:
    headers = graphql_headers(place_id)
    query = {'query': GRAPHQL_QUERY % place_id}
    try:
        resp = http_requests.post(GRAPHQL_URL, headers=headers, json=query, timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if 'errors' in data and 'data' not in data:
            return None
        detail = data.get('data', {}).get('placeDetail')
        return detail if detail else None
    except Exception:
        return None


def parse_graphql_data(detail: dict) -> dict:
    base = detail.get('base') or {}
    images_data = detail.get('images') or {}
    menus_raw = detail.get('menus')  # None=API미반환, []=실제없음
    menus = menus_raw or []
    visitor_reviews = detail.get('visitorReviews') or {}
    visitor_stats = detail.get('visitorReviewStats') or {}
    fsas = detail.get('fsasReviews') or {}
    naver_booking = detail.get('naverBooking') or {}
    homepages = detail.get('homepages') or {}
    naver_order = detail.get('naverOrder') or {}
    keywords = detail.get('keywords') or []
    description = detail.get('description') or ''

    # 영업시간 (newBusinessHours)
    nbh_list = detail.get('newBusinessHours') or []
    bh_items = []
    has_business_hours = False
    has_holiday = False
    if nbh_list and isinstance(nbh_list, list):
        hours_entries = nbh_list[0].get('businessHours') or []
        for entry in hours_entries:
            if not isinstance(entry, dict):
                continue
            bh_items.append(entry)
            inner = entry.get('businessHours')
            if inner and isinstance(inner, dict):
                has_business_hours = True
            if inner is None:
                has_holiday = True

    # 사진
    total_images = images_data.get('totalImages') or 0
    has_photo = total_images > 0

    # 메뉴
    menu_count = len(menus)
    has_price = False
    for m in menus:
        if isinstance(m, dict) and m.get('price') and m['price'].strip():
            has_price = True
            break

    # 리뷰
    visitor_total = visitor_reviews.get('total') or 0
    fsas_total = fsas.get('total') or 0
    review_count = visitor_total + fsas_total
    avg_rating = 0.0
    review_obj = visitor_stats.get('review') or {}
    if isinstance(review_obj, dict):
        avg_rating = float(review_obj.get('avgRating') or 0)

    # 네이버 예약
    booking_url = naver_booking.get('naverBookingUrl') or ''
    has_naver_booking = bool(booking_url)

    # 스마트주문
    order_items = naver_order.get('items') or []
    has_smart_order = bool(order_items)

    # 홈페이지
    homepage_repr = homepages.get('repr') or {}
    homepage_url = homepage_repr.get('url') or ''

    return {
        'name': base.get('name') or '',
        'category': base.get('category') or '',
        'address': base.get('address') or '',
        'road_address': base.get('roadAddress') or '',
        'phone': base.get('phone') or '',
        'business_hours': bh_items,
        'holiday_info': 'Y' if has_holiday else '',
        'description': description,
        'photo_count': total_images,
        'has_representative_photo': has_photo,
        'representative_photo_url': '',
        'menu_count': menu_count,
        'menu_items': menus[:10],
        'has_price': has_price,
        'review_count': review_count,
        'blog_review_count': fsas_total,
        'visitor_review_count': visitor_total,
        'review_rating': avg_rating,
        'has_booking': has_naver_booking,
        'has_naver_booking': has_naver_booking,
        'has_smart_order': has_smart_order,
        'homepage': homepage_url,
        'keywords': keywords,
        '_menus_raw': menus_raw,
    }


def fetch_page_data(place_id: str) -> dict:
    """모바일 페이지의 Apollo State에서 정확한 사진수, 리뷰수, 별점을 가져온다."""
    headers = {
        'User-Agent': (
            'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) '
            'AppleWebKit/605.1.15'
        ),
        'Accept-Language': 'ko-KR,ko;q=0.9',
    }
    result = {}
    # place를 먼저 시도 (가장 범용적), 이후 업종별 카테고리
    best_result = {}
    for cat in ['place', 'restaurant', 'cafe', 'hairshop']:
        try:
            url = f'https://m.place.naver.com/{cat}/{place_id}/home'
            resp = http_requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                continue
            text = resp.text

            cur = {}

            # 사진수: totalImages + clipTotal + sasImagesTotal + cpImages
            ti = re.search(r'"totalImages":(\d+)', text)
            ct = re.search(r'"clipTotal":(\d+)', text)
            sat = re.search(r'"sasImagesTotal":(\d+)', text)
            cpt = re.search(r'cpImages[^}]*"total":(\d+)', text)
            photo_sum = (
                (int(ti.group(1)) if ti else 0)
                + (int(ct.group(1)) if ct else 0)
                + (int(sat.group(1)) if sat else 0)
                + (int(cpt.group(1)) if cpt else 0)
            )
            if photo_sum > 0:
                cur['totalImages'] = photo_sum

            # visitorReviewsTotal, visitorReviewsScore
            m = re.search(
                r'visitorReviewsTotal":(\d+),"visitorReviewsScore":([\d.]+)',
                text,
            )
            if m:
                cur['visitorReviewsTotal'] = int(m.group(1))
                score = float(m.group(2))
                if score > 0:
                    cur['avgRating'] = score

            # avgRating, totalCount from VisitorReviewStats
            m = re.search(
                r'"avgRating":([\d.]+),"totalCount":(\d+)', text
            )
            if m:
                rating = float(m.group(1))
                total = int(m.group(2))
                if rating > 0:
                    cur['avgRating'] = rating
                if total > 0:
                    cur['reviewTotalCount'] = total

            # og:image
            m = re.search(
                r'property="og:image"\s+content="([^"]+)"', text
            )
            if not m:
                m = re.search(
                    r'content="([^"]+)"\s+property="og:image"', text
                )
            if m:
                cur['og_image'] = m.group(1)

            # 더 많은 사진수를 가진 결과를 유지
            if cur.get('totalImages', 0) > best_result.get('totalImages', 0):
                best_result = cur
            elif not best_result and cur:
                best_result = cur

            if best_result:
                break
        except Exception:
            continue
    return best_result


def fetch_og_image(place_id: str) -> str:
    headers = {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15',
        'Accept-Language': 'ko-KR,ko;q=0.9',
    }
    for cat in ['restaurant', 'cafe', 'hairshop', 'place']:
        try:
            url = f'https://m.place.naver.com/{cat}/{place_id}/home'
            resp = http_requests.get(url, headers=headers, timeout=8)
            if resp.status_code == 200:
                m = re.search(r'property="og:image"\s+content="([^"]+)"', resp.text)
                if m:
                    return m.group(1)
                m = re.search(r'content="([^"]+)"\s+property="og:image"', resp.text)
                if m:
                    return m.group(1)
        except Exception:
            continue
    return ''


# ============================================================
# 점수 산정
# ============================================================

def calculate_scores(data: dict) -> dict:
    scores = {}
    recs = []

    def section(key, label, items):
        total = 0
        max_total = 0
        details = []
        for name, ok, pts, mx in items:
            total += pts
            max_total += mx
            details.append({'name': name, 'ok': ok, 'score': pts, 'max': mx})
        scores[key] = {'score': total, 'max': max_total, 'details': details, 'label': label}

    # --- 1) 기본 정보 (25점) ---
    bi = []
    bi.append(('매장명', bool(data['name']), 3 if data['name'] else 0, 3))
    bi.append(('카테고리', bool(data['category']), 3 if data['category'] else 0, 3))
    has_addr = bool(data['road_address'] or data['address'])
    bi.append(('주소', has_addr, 4 if has_addr else 0, 4))
    bi.append(('전화번호', bool(data['phone']), 3 if data['phone'] else 0, 3))
    has_bh = bool(data['business_hours'])
    bi.append(('영업시간', has_bh, 7 if has_bh else 0, 7))
    has_hol = bool(data['holiday_info'])
    bi.append(('휴무일', has_hol, 5 if has_hol else 0, 5))
    section('basic', '기본 정보', bi)
    if not data['phone']:
        recs.append(('mid', '전화번호를 등록하세요. 고객 문의 및 신뢰도에 영향을 줍니다.'))
    if not has_bh:
        recs.append(('high', '영업시간을 반드시 등록하세요! 미등록 시 검색 순위가 크게 하락합니다.'))
    if not has_hol:
        recs.append(('low', '휴무일 정보를 등록하세요. 고객의 헛걸음을 줄여줍니다.'))

    # --- 2) 사진 (15점) ---
    # API는 사장님 등록 사진만 반환 (실제보다 적게 나옴)
    pc = data['photo_count']
    pi = []
    pi.append(('사진 등록', pc > 0, 8 if pc > 0 else 0, 8))
    pi.append(('사진 3장 이상', pc >= 3, 4 if pc >= 3 else 0, 4))
    pi.append(('사진 10장 이상', pc >= 10, 3 if pc >= 10 else 0, 3))
    section('photo', '사진', pi)
    if pc == 0:
        recs.append(('high', '사진을 등록하세요! 검색 결과에서 첫인상을 결정합니다.'))
    elif pc < 3:
        recs.append(('mid', f'사진을 3장 이상 등록하세요. (현재 {pc}장)'))
    elif pc < 10:
        recs.append(('low', f'사진을 10장 이상으로 늘리면 더 좋습니다. (현재 {pc}장)'))

    # --- 3) 메뉴/서비스 ---
    # API에서 메뉴가 null이면(미반환) 채점 제외, 빈 배열이면 실제로 없는 것
    menus_raw = data.get('_menus_raw')  # None=API미반환, []=실제없음
    mc = data['menu_count']
    mi = []
    if menus_raw is None:
        # API가 메뉴 데이터를 반환하지 않음 → 채점 제외 (0/0)
        pass
    else:
        mi.append(('메뉴 등록', mc > 0, 8 if mc > 0 else 0, 8))
        if mc > 0:
            mi.append(('메뉴 5개 이상', mc >= 5, 4 if mc >= 5 else 0, 4))
            mi.append(('가격 정보 포함', data['has_price'], 3 if data['has_price'] else 0, 3))
        else:
            mi.append(('메뉴 5개 이상', False, 0, 4))
            mi.append(('가격 정보 포함', False, 0, 3))
        if mc == 0:
            recs.append(('high', '메뉴를 등록하세요! 메뉴 미등록은 치명적입니다.'))
        elif mc < 5:
            recs.append(('mid', f'메뉴를 5개 이상 등록하세요. (현재 {mc}개)'))
        if not data['has_price'] and mc > 0:
            recs.append(('mid', '메뉴에 가격 정보를 추가하세요. 가격이 있으면 전환율이 높아집니다.'))
    section('menu', '메뉴/서비스', mi)

    # --- 4) 매장 소개 (15점) ---
    desc = data['description'] or ''
    dl = len(desc)
    di = []
    di.append(('소개글 작성', dl > 0, 7 if dl > 0 else 0, 7))
    if dl > 0:
        di.append(('50자 이상', dl >= 50, 4 if dl >= 50 else 0, 4))
        di.append(('150자 이상', dl >= 150, 4 if dl >= 150 else 0, 4))
    else:
        di.append(('50자 이상', False, 0, 4))
        di.append(('150자 이상', False, 0, 4))
    section('description', '매장 소개', di)
    if dl == 0:
        recs.append(('mid', '매장 소개글을 작성하면 검색 키워드 노출에 도움이 됩니다.'))
    elif dl < 50:
        recs.append(('mid', f'소개글을 50자 이상으로 작성하세요. (현재 {dl}자)'))
    elif dl < 150:
        recs.append(('low', '소개글을 150자 이상으로 보강하세요. 키워드를 자연스럽게 포함시키세요.'))

    # --- 5) 리뷰 (20점) ---
    rc = data['review_count']
    if rc == 0:
        rc = data['blog_review_count'] + data['visitor_review_count']
    rating = data['review_rating']
    ri = []
    ri.append(('리뷰 존재', rc > 0, 4 if rc > 0 else 0, 4))
    ri.append(('리뷰 10개 이상', rc >= 10, 4 if rc >= 10 else 0, 4))
    ri.append(('리뷰 50개 이상', rc >= 50, 4 if rc >= 50 else 0, 4))
    ri.append(('리뷰 200개 이상', rc >= 200, 3 if rc >= 200 else 0, 3))
    # 별점: API가 0을 반환하면 채점 제외
    if rating > 0:
        rat_pts = 5 if rating >= 4.0 else 3
        ri.append(('별점 4.0 이상', rating >= 4.0, rat_pts, 5))
    section('review', '리뷰', ri)
    if rc == 0:
        recs.append(('mid', '리뷰를 모으세요. 영수증 리뷰 이벤트를 활용하세요.'))
    elif rc < 10:
        recs.append(('mid', f'리뷰를 10개 이상 모으세요. (현재 {rc}개)'))
    elif rc < 50:
        recs.append(('low', f'리뷰 50개 이상이면 신뢰도가 크게 올라갑니다. (현재 {rc}개)'))
    if 0 < rating < 4.0:
        recs.append(('mid', f'별점을 4.0 이상으로 관리하세요. (현재 {rating:.1f}점)'))

    # --- 6) 부가 기능 (10점) ---
    ei = []
    has_keywords = bool(data.get('keywords'))
    ei.append(('키워드 등록', has_keywords, 4 if has_keywords else 0, 4))
    has_booking = data['has_booking'] or data['has_naver_booking']
    has_order = data['has_smart_order']
    has_homepage = bool(data.get('homepage'))
    # 예약 또는 스마트주문 중 하나라도 있으면 가산
    has_any_service = has_booking or has_order
    ei.append(('예약/주문 연동', has_any_service, 3 if has_any_service else 0, 3))
    ei.append(('홈페이지 등록', has_homepage, 3 if has_homepage else 0, 3))
    section('extra', '부가 기능', ei)
    if not has_keywords:
        recs.append(('mid', '키워드를 등록하세요. 검색 노출 범위가 넓어집니다.'))
    if not has_any_service:
        recs.append(('low', '네이버 예약이나 스마트주문을 연동하면 노출 기회가 늘어납니다.'))

    # --- 총점 계산 ---
    ts = sum(s['score'] for s in scores.values())
    tm = sum(s['max'] for s in scores.values())
    pct = round((ts / tm) * 100, 1) if tm else 0

    if pct >= 90:
        grade, gt, gc = 'S', '최고 수준! 꾸준히 유지하세요.', '#00b894'
    elif pct >= 75:
        grade, gt, gc = 'A', '잘 관리되고 있습니다. 조금만 더 보완하세요.', '#0984e3'
    elif pct >= 60:
        grade, gt, gc = 'B', '보통 수준입니다. 개선이 필요합니다.', '#fdcb6e'
    elif pct >= 40:
        grade, gt, gc = 'C', '미흡합니다. 기본 설정부터 점검하세요.', '#e17055'
    else:
        grade, gt, gc = 'D', '심각하게 부족합니다. 지금 당장 개선이 필요합니다!', '#d63031'

    priority = {'high': 0, 'mid': 1, 'low': 2}
    recs.sort(key=lambda x: priority.get(x[0], 9))

    return {
        'scores': scores,
        'total_score': ts,
        'total_max': tm,
        'percentage': pct,
        'grade': grade,
        'grade_text': gt,
        'grade_color': gc,
        'recommendations': [{'priority': p, 'text': t} for p, t in recs],
        'place_data': {
            'name': data['name'],
            'category': data['category'],
            'address': data['road_address'] or data['address'],
            'phone': data['phone'],
            'photo_count': data['photo_count'],
            'menu_count': data['menu_count'],
            'review_count': data['review_count'],
            'review_rating': data['review_rating'],
            'description_length': len(data['description'] or ''),
            'has_representative_photo': data['has_representative_photo'],
            'representative_photo_url': data.get('representative_photo_url', ''),
            'homepage': data.get('homepage', ''),
            'keywords': data.get('keywords', []),
        },
    }


# ============================================================
# 라우트
# ============================================================

@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/analyze', methods=['POST'])
def analyze():
    url = request.json.get('url', '').strip()
    if not url:
        return jsonify({'error': 'URL을 입력해주세요.'}), 400

    place_id = extract_place_id(url)
    if not place_id:
        return jsonify({'error': '올바른 네이버 플레이스 URL이 아닙니다.\n예: https://m.place.naver.com/restaurant/12345678/home'}), 400

    detail = fetch_place_data(place_id)
    if not detail:
        return jsonify({'error': '장소 정보를 가져올 수 없습니다. URL을 확인해주세요.'}), 400

    data = parse_graphql_data(detail)
    if not data['name']:
        return jsonify({'error': '장소 정보를 파싱할 수 없습니다.'}), 400

    # 페이지 스크래핑으로 정확한 사진수/리뷰수/별점 보정 (더 큰 값 사용)
    page_data = fetch_page_data(place_id)
    page_photos = page_data.get('totalImages', 0)
    data['photo_count'] = max(data['photo_count'], page_photos)
    if data['photo_count'] > 0:
        data['has_representative_photo'] = True
    if page_data.get('reviewTotalCount') and page_data['reviewTotalCount'] > data['review_count']:
        data['review_count'] = page_data['reviewTotalCount']
    if page_data.get('visitorReviewsTotal') and page_data['visitorReviewsTotal'] > data['visitor_review_count']:
        data['visitor_review_count'] = page_data['visitorReviewsTotal']
        if page_data['visitorReviewsTotal'] > data['review_count']:
            data['review_count'] = page_data['visitorReviewsTotal']
    if page_data.get('avgRating') and page_data['avgRating'] > 0:
        data['review_rating'] = page_data['avgRating']
    if page_data.get('og_image'):
        data['representative_photo_url'] = page_data['og_image']
    elif not data.get('representative_photo_url'):
        og_image = fetch_og_image(place_id)
        if og_image:
            data['representative_photo_url'] = og_image

    result = calculate_scores(data)
    result['place_id'] = place_id
    result['url'] = url

    # 사용 로그 저장
    pd = result['place_data']
    log_analysis(
        place_id=place_id, url=url,
        name=pd['name'], category=pd['category'],
        grade=result['grade'], percentage=result['percentage'],
        photo_count=pd['photo_count'], menu_count=pd['menu_count'],
        review_count=pd['review_count'], review_rating=pd['review_rating'],
    )

    return jsonify(result)


@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html')


@app.route('/api/dashboard', methods=['GET'])
def dashboard_api():
    if not _sb_available():
        return jsonify({'error': 'DB 미연결', 'total': 0, 'recent': [],
                        'grade_dist': {}, 'category_dist': {},
                        'daily': [], 'top_places': []})
    try:
        # 전체 데이터 조회 (최근 10000건, 최신순)
        rows = _sb_select('order=created_at.desc&limit=10000')
        total = len(rows)

        # 최근 검색 20건
        recent = []
        for r in rows[:20]:
            recent.append({
                'name': r.get('name', ''),
                'category': r.get('category', ''),
                'grade': r.get('grade', ''),
                'percentage': r.get('percentage', 0),
                'created_at': r.get('created_at', ''),
            })

        # 등급 분포
        grade_dist = {}
        for r in rows:
            g = r.get('grade', '?')
            grade_dist[g] = grade_dist.get(g, 0) + 1

        # 카테고리 분포 (상위 10개)
        cat_dist = {}
        for r in rows:
            c = r.get('category', '') or '미분류'
            # 첫번째 카테고리만 사용
            c = c.split(',')[0].strip() if c else '미분류'
            cat_dist[c] = cat_dist.get(c, 0) + 1
        cat_sorted = sorted(cat_dist.items(), key=lambda x: -x[1])[:10]
        category_dist = dict(cat_sorted)

        # 점수 분포 (10점 단위)
        score_dist = {}
        for r in rows:
            pct = r.get('percentage', 0) or 0
            bucket = int(pct // 10) * 10
            label = f'{bucket}-{bucket + 9}'
            score_dist[label] = score_dist.get(label, 0) + 1

        # 일별 사용량 (최근 30일)
        daily = {}
        for r in rows:
            ca = r.get('created_at', '')
            if ca:
                day = ca[:10]
                daily[day] = daily.get(day, 0) + 1
        daily_sorted = sorted(daily.items())[-30:]

        # 가장 많이 검색된 매장 TOP 10
        place_count = {}
        place_info = {}
        for r in rows:
            pid = r.get('place_id', '')
            if pid:
                place_count[pid] = place_count.get(pid, 0) + 1
                place_info[pid] = {
                    'name': r.get('name', ''),
                    'category': r.get('category', ''),
                    'grade': r.get('grade', ''),
                    'percentage': r.get('percentage', 0),
                }
        top_places = sorted(place_count.items(), key=lambda x: -x[1])[:10]
        top_places_list = []
        for pid, cnt in top_places:
            info = place_info.get(pid, {})
            top_places_list.append({**info, 'count': cnt, 'place_id': pid})

        return jsonify({
            'total': total,
            'recent': recent,
            'grade_dist': grade_dist,
            'category_dist': category_dist,
            'score_dist': score_dist,
            'daily': [{'date': d, 'count': c} for d, c in daily_sorted],
            'top_places': top_places_list,
        })
    except Exception as e:
        return jsonify({'error': str(e), 'total': 0, 'recent': [],
                        'grade_dist': {}, 'category_dist': {},
                        'daily': [], 'top_places': []})
