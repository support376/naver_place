export default async function handler(req, res) {
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    const host = req.headers.host || 'naver-place-14ms.vercel.app';
    const proto = req.headers['x-forwarded-proto'] || 'https';
    const response = await fetch(`${proto}://${host}/api/analyze`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body),
    });

    const data = await response.json();
    return res.status(response.status).json(data);
  } catch (e) {
    return res.status(500).json({ error: '서버 연결에 실패했습니다.' });
  }
}
