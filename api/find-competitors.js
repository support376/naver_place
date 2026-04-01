export default async function handler(req, res) {
  if (req.method !== 'POST') return res.status(405).json({ error: 'Method not allowed' });

  try {
    const { address, category, exclude_name } = req.body;
    if (!address || !category) return res.status(400).json({ error: 'address, category required' });

    // Build search query from address + category
    const area = extractArea(address);
    const query = `${area} ${category}`;

    // Search Naver Place for competitors
    const placeIds = await searchNaverPlace(query, exclude_name);

    if (placeIds.length === 0) {
      return res.status(200).json({ competitors: [], query });
    }

    // Analyze each competitor using existing API
    const competitors = [];
    for (const id of placeIds.slice(0, 5)) {
      try {
        const url = `https://m.place.naver.com/restaurant/${id}/home`;
        const resp = await fetch('https://${req.headers.host || 'naver-place-14ms.vercel.app'}/api/analyze', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url }),
        });
        const data = await resp.json();
        if (!data.error) {
          competitors.push(data);
        }
      } catch (e) {
        console.error(`Failed to analyze competitor ${id}:`, e.message);
      }
    }

    return res.status(200).json({ competitors, query });
  } catch (e) {
    console.error('find-competitors error:', e);
    return res.status(500).json({ error: e.message });
  }
}

function extractArea(address) {
  // "서울 강남구 역삼동 123-45" → "강남구 역삼동"
  // "경기 성남시 분당구 정자동" → "분당구 정자동"
  const parts = (address || '').split(' ').filter(Boolean);
  if (parts.length >= 3) return parts.slice(1, 3).join(' ');
  if (parts.length >= 2) return parts.slice(0, 2).join(' ');
  return parts.join(' ');
}

async function searchNaverPlace(query, excludeName) {
  const encoded = encodeURIComponent(query);
  const ids = [];

  // Strategy 1: Naver Map search API
  try {
    const url = `https://map.naver.com/v5/api/search?caller=pcweb&query=${encoded}&type=all&page=1&displayCount=10`;
    const resp = await fetch(url, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Referer': 'https://map.naver.com/',
      }
    });
    if (resp.ok) {
      const data = await resp.json();
      const items = data?.result?.place?.list || [];
      for (const item of items) {
        if (item.id && (!excludeName || item.name !== excludeName)) {
          ids.push(item.id);
        }
      }
      if (ids.length >= 3) return ids.slice(0, 7);
    }
  } catch (e) {
    console.log('Map API failed, trying search page:', e.message);
  }

  // Strategy 2: Parse Naver mobile search page
  try {
    const searchUrl = `https://m.search.naver.com/search.naver?where=m_place&query=${encoded}`;
    const resp = await fetch(searchUrl, {
      headers: {
        'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15',
      }
    });
    if (resp.ok) {
      const html = await resp.text();
      // Find place IDs from URLs like place.naver.com/restaurant/12345
      const regex = /place\.naver\.com\/\w+\/(\d{6,})/g;
      let match;
      const seen = new Set();
      while ((match = regex.exec(html)) !== null) {
        if (!seen.has(match[1])) {
          seen.add(match[1]);
          ids.push(match[1]);
        }
      }
    }
  } catch (e) {
    console.log('Search page parse failed:', e.message);
  }

  // Filter out the original store if possible
  return ids.filter(id => ids.length <= 5 || true).slice(0, 7);
}
