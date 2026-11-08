import io
import html
import unittest
import app as project

class AppTests(unittest.TestCase):
    def setUp(self):
        self.client = project.app.test_client()

    def test_home(self):
        r = self.client.get('/')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'BloomBot', r.data)
        self.assertIn(b'id="growth-plan-form"', r.data)
        self.assertIn(b"What's slowing you down", r.data)
        self.assertNotIn(b'fake testimonial', r.data.lower())

    def test_landing_sections_have_expected_order(self):
        page = self.client.get('/').get_data(as_text=True)
        order = ['id="approach"', 'id="solutions"', 'id="industries"',
             'id="research"', 'id="stories"',
                 'id="about"']
        positions = [page.index(anchor) for anchor in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('href="#solutions"', page)
        self.assertIn('source_url', project.landing_content()['stats'][0])

    def test_embedded_ted_talks_stay_in_research_container(self):
        page = self.client.get('/').get_data(as_text=True)
        research = page.split('id="research"', 1)[1].split('id="about"', 1)[0]
        self.assertIn('class="research-stories"', research)
        self.assertIn('https://embed.ted.com/talks/', research)
        self.assertEqual(research.count('<iframe '), 2)
        self.assertIn('loading="lazy"', research)
        self.assertIn('Watch on TED', research)
        self.assertIn('rel="noopener noreferrer"', research)

    def test_compact_sections_share_centered_containers(self):
        page = self.client.get('/').get_data(as_text=True)
        approach = page.split('id="approach"', 1)[1].split('id="research"', 1)[0]
        research = page.split('id="research"', 1)[1].split('id="about"', 1)[0]
        self.assertIn('class="container growth-journey"', approach)
        self.assertIn('class="toolkit-inline" id="solutions"', approach)
        self.assertNotIn('</div>\n<div class="toolkit-inline"', approach)
        self.assertIn('class="research-stories" id="stories"', research)
        self.assertIn('class="research-quote"', research)
        self.assertEqual(research.count('<iframe '), 2)

    def test_research_evidence_and_quotes_have_sources(self):
        page = self.client.get('/').get_data(as_text=True)
        research = page.split('id="research"', 1)[1].split('id="about"', 1)[0]
        data = project.landing_content()
        self.assertEqual([stat['value'] for stat in data['stats']], ['36.2M', '58%', '73%'])
        for stat in data['stats']:
            with self.subTest(stat=stat['value']):
                self.assertIn(stat['value'], research)
                self.assertIn(stat['source_url'], research)
                self.assertIn(stat['source_name'], research)
        self.assertIn('surveyed Goldman Sachs small-business program participants', research)
        for name in ('research_quote', 'owner_quote'):
            quote = data[name]
            self.assertIn(html.escape(quote['quote']), research)
            self.assertIn(html.escape(quote['speaker']), research)
            self.assertIn(html.escape(quote['role']).replace('&#x27;', '&#39;'), research)
            self.assertIn(quote['source_url'], research)
        self.assertIn(html.escape("How Andy's Pizza puts AI to work").replace('&#x27;', '&#39;'), research)
        self.assertIn(data['case_study']['source_url'], research)
        self.assertEqual(research.count('<iframe '), 2)
        self.assertEqual(page.count('class="section reveal '), 4)

    def test_dashboard_capabilities_are_on_homepage(self):
        page = self.client.get('/').get_data(as_text=True)
        self.assertIn('These six capabilities are available through the BloomBots AI dashboard', page)
        self.assertIn('href="/dashboard"', page)
        self.assertIn('href="/consult"', page)
        self.assertNotIn('href="/solutions"', page)
        for service in project.landing_content()['services']:
            with self.subTest(service=service['id']):
                self.assertIn(html.escape(service['title']), page)
                self.assertIn(html.escape(service['description']), page)

    def test_dashboard_route(self):
        r = self.client.get('/dashboard')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'id="dashboard"', r.data)
        self.assertIn(b'Calculate your potential value', r.data)
        self.assertNotIn(b'Business health check</button>', r.data)

    def test_solutions(self):
        r = self.client.get('/solutions')
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers['Location'], '/#solutions')

    def test_consult_landing_context(self):
        r = self.client.get('/consult?challenge=finance&industry=restaurant')
        self.assertEqual(r.status_code, 200)
        self.assertIn(b'suggestedProblems', r.data)
        self.assertIn(b'landingContext', r.data)

    def test_bloombots_brand_and_about(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        page = response.get_data(as_text=True)
        self.assertIn('Every business', page)
        self.assertIn('deserves to', page)
        self.assertIn('id="about"', page)
        self.assertIn('SproutBot', page)
        self.assertIn('sproutbot.svg', page)
        self.assertIn('two students who built BloomBots at a hackathon', page)
        self.assertIn('id="growth-plan-form"', page)
        self.assertIn('source_url', project.landing_content()['stats'][0])

    def test_bloombots_solutions_brand(self):
        response = self.client.get('/solutions')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers['Location'], '/#solutions')

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
