"""로컬 개발용 서버"""
from api.index import app

if __name__ == '__main__':
    print('\n  네이버 플레이스 진단 서비스')
    print('  http://localhost:5000 에서 확인하세요\n')
    app.run(debug=True, port=5000)
