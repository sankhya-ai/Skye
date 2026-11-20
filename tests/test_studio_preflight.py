from skye.studio.preflight import inspect


def test_preflight_distinct_direct_sources_and_cache(tmp_path):
    (tmp_path/'assets').mkdir()
    (tmp_path/'assets'/'a b.mp4').write_bytes(b'x'*100)
    cache=tmp_path/'.film/cache';cache.mkdir(parents=True)
    (cache/'proxy.mp4').write_bytes(b'x'*200)
    class Studio:
        def request(self,route):
            return {'project':{'name':'Example','path':str(tmp_path)},'value':{'stage':{'w':360,'h':640},'tracks':[{'clips':[
                {'id':'one','src':'assets/a%20b.mp4#t=1,3','time':[1,3],'speed':2},
                {'id':'two','src':'assets/a b.mp4','at':1,'time':[3,5]},
                {'id':'external','src':'https://example.org/clip.mp4','at':0,'time':[0,1]},
                {'id':'escape','src':'../elsewhere.mp4','at':0,'time':[0,1]},
            ]}]}}
    result=inspect(Studio(),'demo')
    assert result['duration']==3
    assert result['stage']=={'w':360,'h':640}
    assert result['mediaFiles']==1 and result['mediaBytes']==100
    assert result['cacheBytes']==200 and result['freeBytes']>0
    assert 'No reliable time estimate' in result['warning']
    assert 'not nested page dependencies' in result['warning']
