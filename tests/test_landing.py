import json
import re
import unittest
import xml.etree.ElementTree as ET
from tests import test_webstudio
from webstudio.landing import COPY, SLUGS, render, sitemap

class LandingTests(unittest.TestCase):
    def test_localized_content_metadata_and_prices(self):
        titles=set()
        for lang in COPY:
            for slug in SLUGS:
                body=render(lang,slug)
                self.assertEqual(body.count('<h1>'),1)
                title=re.search(r'<title>(.*?)</title>',body)[1]
                self.assertNotIn(title,titles);titles.add(title)
                self.assertIn(f'<html lang="{lang}"',body)
                self.assertEqual(body.count('hreflang='),4)
                schema=json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>',body)[1])
                self.assertEqual(schema['@graph'][2]['offers']['price'],'9.00')
                self.assertIn('$9.00',body)
                self.assertNotIn('aggregateRating',body)
        self.assertEqual(len(ET.fromstring(sitemap())),13)

    def test_public_pages_and_existing_entry_links(self):
        case=test_webstudio.BrowserTests();case.setUp()
        try:
            for path in ['/','/ru/','/en/script/','/uk/products/','/en/reels/']:
                r=case.client.get(path);self.assertEqual(r.status_code,200)
                self.assertIn('landing.js',r.text)
                self.assertNotIn('noindex',r.headers.get('x-robots-tag',''))
            for path in ['/studio','/?payment=example','/?supportTicket=1','/?link_telegram=1']:
                r=case.client.get(path);self.assertEqual(r.status_code,200)
                self.assertIn('id="auth-form"',r.text)
                self.assertIn('noindex',r.headers['x-robots-tag'])
            case.register()
            self.assertIn('id="studio"',case.client.get('/').text)
        finally:case.tearDown()
