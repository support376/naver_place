export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  const apiKey = process.env.ANTHROPIC_API_KEY;
  if (!apiKey) {
    return res.status(500).json({ error: 'ANTHROPIC_API_KEY not configured' });
  }

  try {
    const { place_data, scores, recommendations, grade, percentage } = req.body;
    if (!place_data) {
      return res.status(400).json({ error: 'place_data is required' });
    }

    const pd = place_data;
    const prompt = buildPrompt(pd, scores, recommendations, grade, percentage);

    const response = await fetch('https://api.anthropic.com/v1/messages', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
      },
      body: JSON.stringify({
        model: 'claude-sonnet-4-20250514',
        max_tokens: 4000,
        messages: [{ role: 'user', content: prompt }],
      }),
    });

    const result = await response.json();

    if (result.error) {
      return res.status(500).json({ error: result.error.message || 'AI API error' });
    }

    const text = result.content?.[0]?.text || '';

    // Parse JSON from response
    const jsonMatch = text.match(/\{[\s\S]*\}/);
    if (!jsonMatch) {
      return res.status(500).json({ error: 'AI 응답을 파싱할 수 없습니다.' });
    }

    const aiResult = JSON.parse(jsonMatch[0]);
    return res.status(200).json(aiResult);

  } catch (e) {
    console.error('AI analyze error:', e);
    return res.status(500).json({ error: 'AI 분석 중 오류가 발생했습니다: ' + e.message });
  }
}

function buildPrompt(pd, scores, recs, grade, pct) {
  const name = pd.name || '매장';
  const category = pd.category || '음식점';
  const address = pd.address || '';
  const phone = pd.phone || '';
  const desc = pd.description || '';
  const descLen = pd.description_length || 0;
  const photoCount = pd.photo_count || 0;
  const menuCount = pd.menu_count || 0;
  const reviewCount = pd.review_count || 0;
  const rating = pd.review_rating || 0;

  // Build score summary
  const scoreLines = Object.entries(scores || {}).map(([key, s]) => {
    const pctScore = s.max > 0 ? Math.round((s.score / s.max) * 100) : 0;
    const failedItems = s.details.filter(d => !d.ok).map(d => d.name);
    return `- ${s.label}: ${s.score}/${s.max} (${pctScore}점) ${failedItems.length > 0 ? '/ 미달항목: ' + failedItems.join(', ') : '/ 모두 통과'}`;
  }).join('\n');

  const recLines = (recs || []).map(r => `- [${r.priority}] ${r.text}`).join('\n');

  return `당신은 네이버 플레이스 최적화 전문 컨설턴트입니다. 아래 매장 진단 데이터를 분석하고, JSON 형식으로 응답하세요.

## 매장 정보
- 매장명: ${name}
- 업종: ${category}
- 주소: ${address}
- 전화: ${phone}
- 현재 소개글: "${desc}" (${descLen}자)
- 사진 수: ${photoCount}장
- 메뉴 수: ${menuCount}개
- 리뷰 수: ${reviewCount}개
- 평균 평점: ${rating}
- 종합 등급: ${grade} (${pct}점/100점)

## 카테고리별 점수
${scoreLines}

## 기존 개선 추천사항
${recLines}

## 요청사항
아래 JSON 구조로 응답하세요. 반드시 JSON만 출력하세요. 모든 텍스트는 한국어로 작성하세요.

{
  "seo_analysis": {
    "score": (0-100 숫자),
    "summary": "(현재 소개글의 SEO 문제점 2-3줄 요약)",
    "missing_keywords": ["(이 매장이 노출되어야 할 검색 키워드 5개)"],
    "keyword_density": "(현재 키워드 밀도 분석 한줄)"
  },
  "rewrite": [
    {
      "type": "정보 중심형",
      "text": "(SEO 최적화된 소개글. 200-300자. 지역명+업종+특징 키워드 자연 포함. 이 매장의 실제 정보 기반으로 작성)"
    },
    {
      "type": "감성형",
      "text": "(감성적 소개글. 200-300자. 스토리텔링 방식. 방문 욕구를 자극하는 문체)"
    },
    {
      "type": "키워드 강화형",
      "text": "(키워드 밀도 높은 소개글. 200-300자. 핵심 검색어를 전면에 배치)"
    }
  ],
  "review_analysis": {
    "summary": "(리뷰 현황 분석 2-3줄)",
    "estimated_positive": ["(예상 긍정 키워드 3-5개 - 업종 특성 기반)"],
    "estimated_negative": ["(예상 부정 키워드 2-3개 - 업종 특성 기반)"],
    "response_tip": "(리뷰 답글 작성 팁 2-3줄. 이 업종에 맞는 구체적 조언)"
  },
  "keyword_strategy": {
    "primary_keywords": [
      {"keyword": "(메인 키워드)", "difficulty": "(상/중/하)", "reason": "(추천 이유 한줄)"}
    ],
    "blue_ocean": [
      {"keyword": "(블루오션 키워드)", "difficulty": "하", "monthly_estimate": "(추정 월 검색량)", "reason": "(왜 블루오션인지)"}
    ],
    "avoid": ["(피해야 할 키워드와 이유)"]
  },
  "photo_guide": {
    "current_assessment": "(현재 사진 상태 평가 1-2줄)",
    "must_add": ["(반드시 추가해야 할 사진 종류 3-5개. 구체적으로)"],
    "tips": ["(촬영 팁 3개. 이 업종에 맞는 구체적 조언)"]
  },
  "weekly_plan": [
    {"week": "1주차", "tasks": "(이번 주에 할 일 구체적으로. 3-4가지. 소요시간 포함)"},
    {"week": "2주차", "tasks": "(다음 주에 할 일)"},
    {"week": "3주차", "tasks": "(3주차에 할 일)"},
    {"week": "4주차", "tasks": "(4주차에 할 일)"}
  ],
  "news_tab_ideas": [
    {"title": "(소식탭 발행 제목 1)", "description": "(내용 요약. 어떤 키워드를 포함시킬지)"},
    {"title": "(소식탭 발행 제목 2)", "description": "(내용 요약)"},
    {"title": "(소식탭 발행 제목 3)", "description": "(내용 요약)"},
    {"title": "(소식탭 발행 제목 4)", "description": "(내용 요약)"}
  ],
  "expected_improvement": {
    "score_after": (현재 점수 + 예상 상승분 숫자),
    "grade_after": "(예상 등급)",
    "timeline": "(달성 예상 기간)",
    "monthly_visitor_increase": "(예상 월 추가 방문객 범위)"
  },
  "one_line_verdict": "(이 매장의 핵심 문제를 한 문장으로 요약)"
}`;
}
