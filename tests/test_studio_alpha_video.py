"""Alpha declaration and a real alpha-preserving codec fallback."""
import json
import shutil
import struct
import subprocess
import zlib
from pathlib import Path
import pytest
from test_studio_track_matte import VENDOR, node

def test_alpha_roundtrip_and_implicit_vp9_detection():
    result=node(f'''import {{readFilmFile,filmHtml}} from {json.dumps((VENDOR/'film-doc.mjs').as_uri())};
import {{footageOf}} from {json.dumps((VENDOR/'direct.mjs').as_uri())};
const r=readFilmFile('<body><section><video src="a.webm#t=0,1" alpha></video></section></body>');
if(!r.doc) throw Error(r.problems.join(' '));
console.log(JSON.stringify([readFilmFile(filmHtml(r.doc)).doc.tracks[0].clips[0].alpha,
footageOf({{width:320,height:480,pix_fmt:'yuv420p',tags:{{alpha_mode:'1'}}}})===null]));''')
    assert result==[True,True]

def test_real_codec_fallback_retains_alpha(tmp_path):
    if not shutil.which('ffmpeg') or not shutil.which('node'):
        pytest.skip('requires local ffmpeg and node')
    # Minimal RGBA PNG: left transparent, right opaque, white RGB everywhere.
    def chunk(kind,data):
        return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    rows=b''.join(b'\0'+b''.join(bytes((255,255,255,0 if x<8 else 255)) for x in range(16)) for y in range(16))
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',16,16,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(rows))+chunk(b'IEND',b'')
    (tmp_path/'a.png').write_bytes(png)
    source=tmp_path/'a.mov'
    subprocess.run(['ffmpeg','-v','error','-loop','1','-i',str(tmp_path/'a.png'),'-t','0.12','-c:v','qtrle',str(source)],check=True)
    result=node(f'''import {{seekableCopies}} from {json.dumps((VENDOR/'seekable.mjs').as_uri())};
const source={json.dumps(str(source))};
const copies=await seekableCopies([source],{json.dumps(str(tmp_path))},{{plays:async()=>false}});
console.log(JSON.stringify(copies.get(source)??null));''')
    assert result and result.endswith('.webm')
    decoded=subprocess.run(['ffmpeg','-v','error','-c:v','libvpx-vp9','-i',result,'-frames:v','1','-f','rawvideo','-pix_fmt','rgba','-'],capture_output=True,check=True).stdout
    assert decoded[3] < 5 and decoded[15*4+3] > 250
