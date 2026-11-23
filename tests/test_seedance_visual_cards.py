from tests.test_webstudio import BrowserTests

class SeedanceVisualCardsTests(BrowserTests):
    def test_assets_are_not_limited_to_video_scene_count(self):
        state=self.register()
        self.client.post('/api/event',json={'action':'new:seedance'})
        p=self.app.store.project(self.client.get('/api/state').json()['project']['id'])
        p['data']['script']={'title':'Test','scenes':[{'id':1,'action':'All four','dialogue':'','camera':''}],
                            'assets':[{'id':i,'name':str(i)} for i in range(1,5)]}
        self.app.store.save(p)
        result=self.client.get('/api/state').json()['project']
        self.assertEqual(len(result['scenes']),1)
        self.assertEqual([v['id'] for v in result['visuals']],[1,2,3,4])
