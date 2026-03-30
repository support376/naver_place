"""
네이버 플레이스 진단 서비스 - Vercel Serverless + 로컬 Flask 겸용
"""

import json
import os
import re
import requests as http_requests
from flask import Flask, render_template, request, jsonify

# Flask 템플릿 경로를 프로젝트 루트 기준으로 설정
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__, template_folder=os.path.join(ROOT_DIR, 'templates'))

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
    menus = detail.get('menus') or []
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
    }


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

    def section(key, label, items, max_pts):
        total = 0
        details = []
        for name, ok, pts, mx in items:
            total += pts
            details.append({'name': name, 'ok': ok, 'score': pts, 'max': mx})
        scores[key] = {'score': total, 'max': max_pts, 'details': details, 'label': label}

    # 1) 기본 정보 (20점)
    bi = []
    bi.append(('매장명', bool(data['name']), 2 if data['name'] else 0, 2))
    bi.append(('카테고리', bool(data['category']), 2 if data['category'] else 0, 2))
    has_addr = bool(data['road_address'] or data['address'])
    bi.append(('주소', has_addr, 3 if has_addr else 0, 3))
    bi.append(('전화번호', bool(data['phone']), 3 if data['phone'] else 0, 3))
    has_bh = bool(data['business_hours'])
    bi.append(('영업시간', has_bh, 5 if has_bh else 0, 5))
    has_hol = bool(data['holiday_info'])
    bi.append(('휴무일', has_hol, 5 if has_hol else 0, 5))
    section('basic', '기본 정보', bi, 20)
    if not data['category']:
        recs.append(('high', '카테고리를 정확하게 설정하세요. 검색 노출에 직접적인 영향을 줍니다.'))
    if not data['phone']:
        recs.append(('mid', '전화번호를 등록하세요. 고객 문의 및 신뢰도에 영향을 줍니다.'))
    if not has_bh:
        recs.append(('high', '영업시간을 반드시 등록하세요! 미등록 시 검색 순위가 크게 하락합니다.'))
    if not has_hol:
        recs.append(('low', '휴무일 정보를 등록하세요. 고객의 헛걸음을 줄여줍니다.'))

    # 2) 사진 (20점)
    pc = data['photo_count']
    pi = []
    pi.append(('대표사진', data['has_representative_photo'],
               5 if data['has_representative_photo'] else 0, 5))
    pi.append(('사진 5장 이상', pc >= 5, 5 if pc >= 5 else 0, 5))
    pi.append(('사진 15장 이상', pc >= 15, 5 if pc >= 15 else 0, 5))
    pi.append(('사진 30장 이상', pc >= 30, 5 if pc >= 30 else 0, 5))
    section('photo', '사진', pi, 20)
    if not data['has_representative_photo']:
        recs.append(('high', '대표사진을 설정하세요! 검색 결과에서 첫인상을 결정합니다.'))
    if pc < 5:
        recs.append(('high', f'사진을 더 등록하세요. (현재 {pc}장 → 최소 5장 권장)'))
    elif pc < 15:
        recs.append(('mid', f'사진을 15장 이상으로 늘리세요. (현재 {pc}장)'))
    elif pc < 30:
        recs.append(('low', f'사진 30장 이상이면 상위 노출에 유리합니다. (현재 {pc}장)'))

    # 3) 메뉴/서비스 (20점)
    mc = data['menu_count']
    mi = []
    mi.append(('메뉴 등록', mc > 0, 8 if mc > 0 else 0, 8))
    mi.append(('메뉴 5개 이상', mc >= 5, 4 if mc >= 5 else 0, 4))
    mi.append(('메뉴 10개 이상', mc >= 10, 3 if mc >= 10 else 0, 3))
    mi.append(('가격 정보 포함', data['has_price'], 5 if data['has_price'] else 0, 5))
    section('menu', '메뉴/서비스', mi, 20)
    if mc == 0:
        recs.append(('high', '메뉴를 등록하세요! 메뉴 미등록은 치명적입니다.'))
    elif mc < 5:
        recs.append(('mid', f'메뉴를 5개 이상 등록하세요. (현재 {mc}개)'))
    if not data['has_price'] and mc > 0:
        recs.append(('mid', '메뉴에 가격 정보를 추가하세요. 가격이 있으면 전환율이 높아집니다.'))

    # 4) 매장 소개 (15점)
    desc = data['description'] or ''
    dl = len(desc)
    di = []
    di.append(('소개글 작성', dl > 0, 7 if dl > 0 else 0, 7))
    di.append(('50자 이상', dl >= 50, 4 if dl >= 50 else 0, 4))
    di.append(('150자 이상', dl >= 150, 4 if dl >= 150 else 0, 4))
    section('description', '매장 소개', di, 15)
    if dl == 0:
        recs.append(('high', '매장 소개글을 작성하세요! 검색 키워드 노출에 중요합니다.'))
    elif dl < 50:
        recs.append(('mid', f'소개글을 50자 이상으로 작성하세요. (현재 {dl}자)'))
    elif dl < 150:
        recs.append(('low', '소개글을 150자 이상으로 보강하세요. 키워드를 자연스럽게 포함시키세요.'))

    # 5) 리뷰 (15점)
    rc = data['review_count']
    if rc == 0:
        rc = data['blog_review_count'] + data['visitor_review_count']
    rating = data['review_rating']
    ri = []
    ri.append(('리뷰 10개 이상', rc >= 10, 5 if rc >= 10 else 0, 5))
    ri.append(('리뷰 50개 이상', rc >= 50, 5 if rc >= 50 else 0, 5))
    rat_pts = 5 if rating >= 4.0 else (2 if rating > 0 else 0)
    ri.append(('별점 4.0 이상', rating >= 4.0, rat_pts, 5))
    section('review', '리뷰', ri, 15)
    if rc < 10:
        recs.append(('mid', f'리뷰를 10개 이상 모으세요. (현재 {rc}개) 영수증 리뷰 이벤트를 활용하세요.'))
    elif rc < 50:
        recs.append(('low', f'리뷰 50개 이상이면 신뢰도가 크게 올라갑니다. (현재 {rc}개)'))
    if 0 < rating < 4.0:
        recs.append(('mid', f'별점을 4.0 이상으로 관리하세요. (현재 {rating:.1f}점)'))

    # 6) 부가 기능 (10점)
    ei = []
    has_keywords = bool(data.get('keywords'))
    ei.append(('키워드 등록', has_keywords, 3 if has_keywords else 0, 3))
    ei.append(('네이버 예약', data['has_booking'] or data['has_naver_booking'],
               4 if (data['has_booking'] or data['has_naver_booking']) else 0, 4))
    ei.append(('스마트주문', data['has_smart_order'],
               3 if data['has_smart_order'] else 0, 3))
    section('extra', '부가 기능', ei, 10)
    if not has_keywords:
        recs.append(('mid', '키워드를 등록하세요. 검색 노출 범위가 넓어집니다.'))
    if not (data['has_booking'] or data['has_naver_booking']):
        recs.append(('mid', '네이버 예약을 연동하세요. 예약 가능 매장은 검색 상위에 노출됩니다.'))
    if not data['has_smart_order']:
        recs.append(('low', '스마트주문을 연동하면 추가 노출 기회를 얻을 수 있습니다.'))

    # 총점
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

    og_image = fetch_og_image(place_id)
    if og_image:
        data['representative_photo_url'] = og_image

    result = calculate_scores(data)
    result['place_id'] = place_id
    result['url'] = url
    return jsonify(result)
