import http.client
import json
import threading
from http.server import ThreadingHTTPServer
import pytest
from skye.ui.server import UiHandler
from skye.media.api import snapshot
from test_video_model import project


@pytest.fixture
def server(project):
    server=ThreadingHTTPServer(("127.0.0.1",0),UiHandler)
    server.state_dir=project.parent.parent
    server.token="test-token"
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield server,project
    server.shutdown();server.server_close();thread.join()


def request(server,method,path,body=None,headers=None):
    client=http.client.HTTPConnection(*server.server_address,timeout=5)
    h={"X-Skye-Token":"test-token",**(headers or {})}
    data=json.dumps(body).encode() if body is not None else None
    client.request(method,path,data,h)
    result=client.getresponse();status=result.status;payload=json.loads(result.read());client.close()
    return status,payload


def test_video_auth_and_no_token_in_urls(server):
    s,root=server
    status,_=request(s,"GET","/api/media?token=test-token",headers={"X-Skye-Token":""})
    assert status==403
    status,data=request(s,"GET","/api/media")
    assert status==200 and len(data["projects"])==1
    assert "test-token" not in json.dumps(data)
    status,_=request(s,"POST",f"/api/media/{root.name}/render",{},headers={"Origin":"https://outside.invalid"})
    assert status==403


def test_video_api_scope_and_stale(server):
    s,root=server
    status,p=request(s,"GET",f"/api/media/{root.name}")
    assert status==200
    status,u=request(s,"GET",f"/api/media/{root.name}/units/shot-0001")
    assert status==200
    f=u["files"];f["captions.json"]='[{"start":0,"end":30,"text":"New caption"}]'
    body={"shot":"shot-0001","revision":p["revision"],"files":f}
    assert request(s,"POST",f"/api/media/{root.name}/save",body)[0]==200
    assert request(s,"POST",f"/api/media/{root.name}/save",body)[0]==400
    assert request(s,"GET",f"/api/media/{root.name}/units/../../private")[0]==404


def test_prepare_does_not_run_a_model(server,monkeypatch):
    s,root=server
    from skye.media import worker
    monkeypatch.setattr(worker,"run_edit",lambda *a,**kw: (_ for _ in ()).throw(AssertionError("not approved")))
    status,proposal=request(s,"POST",f"/api/media/{root.name}/prepare",{"shot":"shot-0001","goal":"A shorter title"})
    assert status==200
    assert proposal["shot"]=="shot-0001"
