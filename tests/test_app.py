import io
import unittest
import app as project

class AppTests(unittest.TestCase):
    def setUp(self):
        self.client = project.app.test_client()

    def test_home(self):
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Northstar', r.data)

    def test_recommendations(self):
        r = self.client.post('/api/recommend', json={'industry':'coffee','problem':'retention', 'software':'Square','budget':'75','comfort':'beginner'})
        self.assertEqual(r.status_code,200)
        data=r.get_json()
        self.assertEqual(data['problem'],'retention')
        self.assertGreaterEqual(len(data['tools']),3)
        self.assertEqual(data['tools'][0]['name'],'Square Marketing')

    def test_analyze_csv(self):
        csv=b'customer_id,date,amount\nA,2026-10-01,10\nA,2026-10-05,10\nB,2026-10-03,8\n'
        r=self.client.post('/api/analyze',data={'file':(io.BytesIO(csv),'sample.csv')},content_type='multipart/form-data')
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.get_json()['repeat_rate'], 50.0)

    def test_no_external_domain_discovery(self):
        r=self.client.post('/api/discover',json={'url':'http://localhost:5000/secrets'})
        self.assertEqual(r.status_code,400)
        r=self.client.post('/api/discover',json={'url':'https://example.com/'})
        self.assertEqual(r.status_code,400)

    def test_preview_without_api_key(self):
        r=self.client.post('/api/preview',json={'business':'Test Coffee'})
        self.assertEqual(r.status_code,200)
        self.assertIn('Test Coffee',r.get_json()['content'])

if __name__ == '__main__':
    unittest.main()
