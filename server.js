const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

const PORT = Number(process.env.PORT || 3000);
const PUBLIC_DIR = path.join(__dirname, "public");
const INDEX_FILE = path.join(PUBLIC_DIR, "index.html");

const API_TARGETS = {
  "/api/place": "https://naver-place-14ms.vercel.app/api/analyze",
  "/api/threads": "https://thread-tawny-sigma.vercel.app/api/analyze",
};

const ANTHROPIC_API_KEY = process.env.ANTHROPIC_API_KEY || "";
const TOSS_CLIENT_KEY = process.env.TOSS_CLIENT_KEY || "test_ck_D5GePWvyJnrK0W0k6q8gmeYblrqG"; // test key
const TOSS_SECRET_KEY = process.env.TOSS_SECRET_KEY || "test_sk_zXLkKEypNArWmo50nX3lmeaxYG5R"; // test key
const NOTIFY_EMAIL = "first@dreamframe.org";
const DATA_DIR = path.join(__dirname, "data");

// Ensure data directory exists
if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });

// Simple file-based order/request storage
function saveData(type, data) {
  const file = path.join(DATA_DIR, `${type}.json`);
  let existing = [];
  try { existing = JSON.parse(fs.readFileSync(file, "utf-8")); } catch {}
  existing.push({ ...data, _ts: new Date().toISOString() });
  fs.writeFileSync(file, JSON.stringify(existing, null, 2));
  return existing.length;
}

const MIME_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".svg": "image/svg+xml",
  ".ico": "image/x-icon",
  ".txt": "text/plain; charset=utf-8",
};

function sendJson(res, statusCode, payload) {
  res.writeHead(statusCode, { "Content-Type": "application/json; charset=utf-8" });
  res.end(JSON.stringify(payload));
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    let data = "";
    req.on("data", (chunk) => {
      data += chunk;
    });
    req.on("end", () => resolve(data));
    req.on("error", reject);
  });
}

async function handleProxy(req, res, targetUrl) {
  if (req.method !== "POST") {
    sendJson(res, 405, { error: "Method not allowed" });
    return;
  }

  try {
    const rawBody = await readBody(req);
    const response = await fetch(targetUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: rawBody,
    });
    const text = await response.text();

    res.writeHead(response.status, {
      "Content-Type": response.headers.get("content-type") || "application/json; charset=utf-8",
    });
    res.end(text);
  } catch {
    sendJson(res, 500, { error: "서버 연결에 실패했습니다." });
  }
}

// ═══ AGENCY REQUEST + EMAIL ═══
async function handleAgencyRequest(req, res) {
  if (req.method !== "POST") { sendJson(res, 405, { error: "Method not allowed" }); return; }
  try {
    const raw = await readBody(req);
    const data = JSON.parse(raw);

    // Save to file
    const count = saveData("agency-requests", data);
    console.log(`[Agency] Request #${count} saved: ${data.store_name} / ${data.phone}`);

    // Send email via FormSubmit
    try {
      await fetch("https://formsubmit.co/ajax/" + NOTIFY_EMAIL, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Accept": "application/json" },
        body: JSON.stringify({
          _subject: "[플레이스닥터] 대행 신청 - " + (data.store_name || "매장"),
          ...data
        })
      });
    } catch (e) { console.log("FormSubmit failed (may need activation):", e.message); }

    // Also try direct notification via simple webhook log
    console.log("=== AGENCY REQUEST ===");
    console.log(JSON.stringify(data, null, 2));
    console.log("======================");

    sendJson(res, 200, { success: true, id: count });
  } catch (e) {
    sendJson(res, 500, { error: e.message });
  }
}

// ═══ TOSS PAYMENTS ═══
const orders = new Map(); // in-memory order store

async function handleCreateOrder(req, res) {
  if (req.method !== "POST") { sendJson(res, 405, { error: "Method not allowed" }); return; }
  try {
    const raw = await readBody(req);
    const { tier, storeName, storeUrl } = JSON.parse(raw);

    const prices = { 1: 19900, 2: 49900, 3: 299000 };
    const names = { 1: "종합 진단 리포트", 2: "경쟁 비교 + 액션 가이드", 3: "대행 서비스" };
    const amount = prices[tier];
    const orderName = names[tier];
    if (!amount) { sendJson(res, 400, { error: "Invalid tier" }); return; }

    const orderId = "PD-" + Date.now() + "-" + Math.random().toString(36).slice(2, 8);

    orders.set(orderId, { tier, amount, orderName, storeName, storeUrl, paid: false, created: new Date().toISOString() });
    saveData("orders", { orderId, tier, amount, orderName, storeName, storeUrl });

    sendJson(res, 200, { orderId, amount, orderName, clientKey: TOSS_CLIENT_KEY });
  } catch (e) {
    sendJson(res, 500, { error: e.message });
  }
}

async function handlePaymentSuccess(req, res) {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const paymentKey = url.searchParams.get("paymentKey");
  const orderId = url.searchParams.get("orderId");
  const amount = Number(url.searchParams.get("amount"));

  const order = orders.get(orderId);
  if (!order) {
    res.writeHead(302, { Location: "/?payment=error&msg=order_not_found" });
    res.end(); return;
  }

  // Verify with Toss API
  try {
    const auth = Buffer.from(TOSS_SECRET_KEY + ":").toString("base64");
    const verifyResp = await fetch("https://api.tosspayments.com/v1/payments/confirm", {
      method: "POST",
      headers: { "Authorization": "Basic " + auth, "Content-Type": "application/json" },
      body: JSON.stringify({ paymentKey, orderId, amount })
    });
    const verifyData = await verifyResp.json();

    if (verifyData.status === "DONE") {
      order.paid = true;
      order.paymentKey = paymentKey;
      saveData("payments", { orderId, paymentKey, amount, tier: order.tier, status: "DONE" });
      console.log(`[Payment] SUCCESS: ${orderId} tier=${order.tier} amount=${amount}`);

      // If tier 3 (agency), send notification
      if (order.tier === 3) {
        console.log("[Payment] Agency service paid — notification needed");
      }

      res.writeHead(302, { Location: "/?payment=success&tier=" + order.tier + "&orderId=" + orderId });
      res.end();
    } else {
      res.writeHead(302, { Location: "/?payment=error&msg=" + encodeURIComponent(verifyData.message || "verification_failed") });
      res.end();
    }
  } catch (e) {
    console.error("[Payment] Verify error:", e);
    res.writeHead(302, { Location: "/?payment=error&msg=verify_error" });
    res.end();
  }
}

async function handlePaymentFail(req, res) {
  const url = new URL(req.url, `http://${req.headers.host}`);
  const code = url.searchParams.get("code");
  const msg = url.searchParams.get("message");
  console.log(`[Payment] FAIL: code=${code} msg=${msg}`);
  res.writeHead(302, { Location: "/?payment=fail&msg=" + encodeURIComponent(msg || "결제 실패") });
  res.end();
}

async function handleFindCompetitors(req, res) {
  if (req.method !== "POST") { sendJson(res, 405, { error: "Method not allowed" }); return; }
  try {
    const raw = await readBody(req);
    const { address, category, exclude_name } = JSON.parse(raw);
    if (!address || !category) { sendJson(res, 400, { error: "address, category required" }); return; }

    const area = (address || "").split(" ").filter(Boolean).slice(1, 3).join(" ");
    const query = `${area} ${category}`;
    const encoded = encodeURIComponent(query);

    // Search Naver Map API for place IDs
    let placeIds = [];
    try {
      const mapResp = await fetch(`https://map.naver.com/v5/api/search?caller=pcweb&query=${encoded}&type=all&page=1&displayCount=10`, {
        headers: { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36", "Referer": "https://map.naver.com/" }
      });
      if (mapResp.ok) {
        const mapData = await mapResp.json();
        const items = mapData?.result?.place?.list || [];
        for (const item of items) {
          if (item.id && item.name !== exclude_name) placeIds.push({ id: item.id, name: item.name });
        }
      }
    } catch (e) { console.log("Map API failed:", e.message); }

    // Fallback: parse search page
    if (placeIds.length < 3) {
      try {
        const searchResp = await fetch(`https://m.search.naver.com/search.naver?where=m_place&query=${encoded}`, {
          headers: { "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15" }
        });
        if (searchResp.ok) {
          const html = await searchResp.text();
          const regex = /place\.naver\.com\/\w+\/(\d{6,})/g;
          const seen = new Set(placeIds.map(p => p.id));
          let match;
          while ((match = regex.exec(html)) !== null) {
            if (!seen.has(match[1])) { seen.add(match[1]); placeIds.push({ id: match[1], name: null }); }
          }
        }
      } catch (e) { console.log("Search fallback failed:", e.message); }
    }

    // Analyze top 5 competitors
    const competitors = [];
    const analyzeUrl = "https://naver-place-14ms.vercel.app/api/analyze";
    for (const place of placeIds.slice(0, 5)) {
      try {
        const placeUrl = `https://m.place.naver.com/restaurant/${place.id}/home`;
        const resp = await fetch(analyzeUrl, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: placeUrl })
        });
        const data = await resp.json();
        if (!data.error) competitors.push(data);
      } catch (e) { console.log(`Competitor ${place.id} failed:`, e.message); }
    }

    sendJson(res, 200, { competitors, query, found: placeIds.length });
  } catch (e) {
    sendJson(res, 500, { error: "경쟁사 검색 오류: " + e.message });
  }
}

async function handleAiAnalyze(req, res) {
  if (req.method !== "POST") { sendJson(res, 405, { error: "Method not allowed" }); return; }
  if (!ANTHROPIC_API_KEY) { sendJson(res, 500, { error: "ANTHROPIC_API_KEY not set. Set it: ANTHROPIC_API_KEY=sk-ant-... node server.js" }); return; }

  try {
    const rawBody = await readBody(req);
    const body = JSON.parse(rawBody);
    const { place_data: pd, scores, recommendations: recs, grade, percentage: pct } = body;
    if (!pd) { sendJson(res, 400, { error: "place_data required" }); return; }

    const prompt = buildAiPrompt(pd, scores, recs, grade, pct);

    const response = await fetch("https://api.anthropic.com/v1/messages", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
      },
      body: JSON.stringify({
        model: "claude-sonnet-4-20250514",
        max_tokens: 4000,
        messages: [{ role: "user", content: prompt }],
      }),
    });

    const result = await response.json();
    if (result.error) { sendJson(res, 500, { error: result.error.message || "AI API error" }); return; }

    const text = result.content?.[0]?.text || "";
    const jsonMatch = text.match(/\{[\s\S]*\}/);
    if (!jsonMatch) { sendJson(res, 500, { error: "AI 응답 파싱 실패" }); return; }

    sendJson(res, 200, JSON.parse(jsonMatch[0]));
  } catch (e) {
    sendJson(res, 500, { error: "AI 분석 오류: " + e.message });
  }
}

function buildAiPrompt(pd, scores, recs, grade, pct) {
  const name = pd.name || "매장";
  const category = pd.category || "음식점";
  const address = pd.address || "";
  const desc = pd.description || "";
  const descLen = pd.description_length || 0;
  const photoCount = pd.photo_count || 0;
  const menuCount = pd.menu_count || 0;
  const reviewCount = pd.review_count || 0;
  const rating = pd.review_rating || 0;

  const scoreLines = Object.entries(scores || {}).map(([k, s]) => {
    const p = s.max > 0 ? Math.round((s.score / s.max) * 100) : 0;
    const fails = s.details.filter(d => !d.ok).map(d => d.name);
    return `- ${s.label}: ${s.score}/${s.max} (${p}점)${fails.length ? " / 미달: " + fails.join(", ") : ""}`;
  }).join("\n");
  const recLines = (recs || []).map(r => `- [${r.priority}] ${r.text}`).join("\n");

  return `당신은 네이버 플레이스 최적화 전문 컨설턴트입니다. 아래 매장 데이터를 분석하고 **반드시 JSON만** 출력하세요. 설명 텍스트 없이 JSON 객체 하나만 반환하세요.

## 매장 정보
- 매장명: ${name} / 업종: ${category} / 주소: ${address}
- 소개글: "${desc}" (${descLen}자) / 사진: ${photoCount}장 / 메뉴: ${menuCount}개
- 리뷰: ${reviewCount}개 / 평점: ${rating} / 등급: ${grade} (${pct}/100)

## 점수
${scoreLines}

## 기존 개선사항
${recLines}

아래 JSON을 정확히 채워서 반환하세요:
{
  "one_line_verdict": "(핵심 문제 한 문장 요약)",
  "intro_analysis": {
    "score": (0-100),
    "summary": "(현재 소개글의 문제점 2줄. 네이버 플레이스 알고리즘 관점에서)",
    "missing_keywords": ["(소개글에 포함되어야 할 핵심 검색 키워드 5개)"]
  },
  "rewrite": [
    {"type": "정보 중심형", "text": "(200-300자. 지역+업종+특징 키워드 포함. 실제 매장 정보 기반)"},
    {"type": "감성형", "text": "(200-300자. 스토리텔링. 방문 욕구 자극)"},
    {"type": "키워드 강화형", "text": "(200-300자. 핵심 검색어 전면 배치)"}
  ],
  "review_analysis": {
    "summary": "(리뷰 현황 분석 2줄)",
    "estimated_positive": ["(업종 기반 예상 긍정 키워드 4개)"],
    "estimated_negative": ["(업종 기반 예상 부정 키워드 3개)"],
    "response_tip": "(리뷰 답글 작성 팁 2줄)"
  },
  "keyword_strategy": {
    "primary": [
      {"keyword": "(메인 키워드)", "difficulty": "(상/중/하)", "reason": "(한줄)"},
      {"keyword": "", "difficulty": "", "reason": ""},
      {"keyword": "", "difficulty": "", "reason": ""}
    ],
    "blue_ocean": [
      {"keyword": "(블루오션)", "difficulty": "하", "monthly_est": "(추정 검색량)", "reason": ""},
      {"keyword": "", "difficulty": "하", "monthly_est": "", "reason": ""}
    ]
  },
  "photo_guide": {
    "assessment": "(현재 사진 평가 1줄)",
    "must_add": ["(추가할 사진 종류 4개. 구체적)"],
    "tips": ["(촬영 팁 3개. 업종 맞춤)"]
  },
  "weekly_plan": [
    {"week": "1주차", "tasks": "(할일 3-4가지. 소요시간 포함)"},
    {"week": "2주차", "tasks": ""},
    {"week": "3주차", "tasks": ""},
    {"week": "4주차", "tasks": ""}
  ],
  "news_ideas": [
    {"title": "(소식탭 제목)", "desc": "(내용+키워드)"},
    {"title": "", "desc": ""},
    {"title": "", "desc": ""},
    {"title": "", "desc": ""}
  ],
  "expected": {
    "score_after": (숫자),
    "grade_after": "(등급)",
    "timeline": "(기간)",
    "visitor_increase": "(추가 방문객)"
  }
}`;
}

function serveFile(res, filePath) {
  fs.readFile(filePath, (error, data) => {
    if (error) {
      sendJson(res, 404, { error: "Not found" });
      return;
    }

    const ext = path.extname(filePath).toLowerCase();
    res.writeHead(200, { "Content-Type": MIME_TYPES[ext] || "application/octet-stream" });
    res.end(data);
  });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`);

  // AI analyze endpoint
  if (url.pathname === "/api/ai-analyze") { await handleAiAnalyze(req, res); return; }
  // Competitor finder
  if (url.pathname === "/api/find-competitors") { await handleFindCompetitors(req, res); return; }
  // Agency request (email notification)
  if (url.pathname === "/api/agency-request") { await handleAgencyRequest(req, res); return; }
  // Toss payment endpoints
  if (url.pathname === "/api/create-order") { await handleCreateOrder(req, res); return; }
  if (url.pathname === "/api/toss-config") { sendJson(res, 200, { clientKey: TOSS_CLIENT_KEY }); return; }
  if (url.pathname === "/payment/success") { await handlePaymentSuccess(req, res); return; }
  if (url.pathname === "/payment/fail") { await handlePaymentFail(req, res); return; }

  if (API_TARGETS[url.pathname]) {
    await handleProxy(req, res, API_TARGETS[url.pathname]);
    return;
  }

  const requestedPath = url.pathname === "/" ? INDEX_FILE : path.join(PUBLIC_DIR, url.pathname);
  const normalizedPath = path.normalize(requestedPath);

  if (!normalizedPath.startsWith(PUBLIC_DIR)) {
    sendJson(res, 403, { error: "Forbidden" });
    return;
  }

  serveFile(res, normalizedPath);
});

server.listen(PORT, () => {
  console.log(`Local server running at http://localhost:${PORT}`);
});
