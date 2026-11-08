import io
import unittest
import app as project

class AppTests(unittest.TestCase):
    def setUp(self):
        self.client = project.app.test_client()

    def test_home(self):
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'BloomBot', r.data)
        self.assertIn(b'id="challenge-form"', r.data)
        self.assertIn(b"What's holding back", r.data)
        self.assertIn(b'/consult?challenge=', r.data)
        self.assertNotIn(b'fake testimonial', r.data.lower())

    def test_landing_has_five_intentional_sections(self):
        page = self.client.get('/').get_data(as_text=True)
        order = ['id="industries"', 'id="challenges"', 'id="approach"',
                 'id="solutions"', 'id="research"', 'id="stories"',
                 'id="about"', 'id="trust"']
        positions = [page.index(anchor) for anchor in order]
        self.assertEqual(positions, sorted(positions))
        self.assertEqual(page.count('class="section reveal '), 5)
        self.assertIn('href="/consult?industry=', page)
        self.assertIn('href="/solutions#', page)
        self.assertIn('source_url', project.landing_content()['stats'][0])
        self.assertIn('not BloomBots testimonials or endorsements', page)

    def test_dashboard_route(self):
        r = self.client.get('/dashboard')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'id="dashboard"', r.data)
        self.assertIn(b'Calculate your potential value', r.data)
        self.assertNotIn(b'Business health check</button>', r.data)

    def test_solutions(self):
        r = self.client.get('/solutions')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'Cost &amp; value planning', r.data)
        self.assertIn(b'What does', r.data)
        self.assertIn(b'id="verification"', r.data)

    def test_consult_landing_context(self):
        r = self.client.get('/consult?challenge=finance&industry=restaurant')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'suggestedProblems', r.data)
        self.assertIn(b'landingContext', r.data)

    def test_industry_cards_prefill_relevant_problems(self):
        response = self.client.get('/consult?industry=restaurant')
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        expected = {
            'restaurant': ('restaurant or café', 'reservations, staff schedules'),
            'retail': ('retail shop or boutique', 'keeping inventory accurate'),
            'services': ('professional services business', 'collecting new client details'),
            'hvac': ('home services business', 'schedule technicians'),
            'landscaping': ('landscaping business', 'coordinate crew schedules'),
            'other': ('small business', 'repetitive tasks'),
        }
        for industry, (business_type, problem_fragment) in expected.items():
            with self.subTest(industry=industry):
                self.assertIn(f"{industry}:{{type:'{business_type}'", page)
                self.assertIn(problem_fragment, page)
        self.assertIn("if(!challenge||!Object.hasOwn(suggestedProblems,challenge))", page)
        self.assertIn('example.problem;', page)

    def test_bloombots_brand_and_about(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('Every business', page)
        self.assertIn('deserves to', page)
        self.assertIn('id="about"', page)
        self.assertIn('SproutBot', page)
        self.assertIn('sproutbot.svg', page)
        self.assertIn('early-stage hackathon project', page)
        self.assertIn('id="challenge-form"', page)
        self.assertIn('href="/consult?industry=', page)
        self.assertIn('source_url', project.landing_content()['stats'][0])

    def test_bloombots_solutions_brand(self):
        response = self.client.get('/solutions')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'BloomBots', response.data)
        self.assertIn(b'sproutbot.svg', response.data)
        self.assertIn(b'id="verification"', response.data)

    def test_recommendations(self):
        r = self.client.post('/api/recommend', json={'industry':'coffee','problem':'retention', 'software':'Square','budget':'75','comfort':'beginner'})
        self.assertEqual(r.status_code,200)
        data=r.get_json()
        self.assertEqual(data['problem'],'retention')
        self.assertGreaterEqual(len(data['tools']),3)
        self.assertEqual(data['tools'][0]['name'],'Square Marketing')

    def test_page_is_simplified(self):
        r = self.client.get('/dashboard')
        self.assertNotIn(b'class="score"', r.data)
        self.assertNotIn(b'name="consent"><option value="unsure"', r.data)
        self.assertIn(b'Add details (optional)', r.data)

    def test_recommendation_includes_catalog_price(self):
        r = self.client.post('/api/recommend', json={'industry':'coffee','problem':'marketing','software':'Square','budget':'30'})
        tools = {t['id']: t for t in r.get_json()['tools']}
        square = tools['square-marketing']['price_estimate']
        self.assertEqual((square['plan'], square['monthly_cost'], square['within_budget']), ('Square Plus', 49.0, False))
        self.assertTrue(square['source_url'].startswith('https://'))

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
