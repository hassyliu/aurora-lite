"""Upload a complete draft, then publish it; never replace a published release."""
import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request


def main():
    repository = os.environ['GITHUB_REPOSITORY']
    commit = os.environ['GITHUB_SHA']
    token = os.environ['GITHUB_TOKEN']
    tag = 'v0.3.0'
    output = Path('dist/release')
    required = {'aurora-lite-0.3.0-linux-amd64-debian13.tar.gz',
                'aurora-lite-0.3.0-linux-amd64-debian13.tar.gz.sha256',
                'aurora-lite-0.3.0-source.tar.gz', 'aurora-lite-0.3.0-source.tar.gz.sha256',
                'UPGRADE-0.3.0.md', 'BUILD-INFO.json'}
    if {path.name for path in output.iterdir() if path.is_file()} != required:
        raise RuntimeError('Release assets are missing or unexpected; refusing to publish')
    for path in output.glob('*.tar.gz'):
        expected = path.with_name(path.name+'.sha256').read_text().split()[0]
        if expected != hashlib.sha256(path.read_bytes()).hexdigest():
            raise RuntimeError('Checksum mismatch: '+path.name)
    base = 'https://api.github.com/repos/'+repository
    def request(method, url, payload=None, binary=False):
        data = payload if binary else (json.dumps(payload).encode() if payload is not None else None)
        headers = {'Authorization':'Bearer '+token, 'Accept':'application/vnd.github+json',
                   'User-Agent':'aurora-lite-release', 'X-GitHub-Api-Version':'2026-03-10',
                   'Content-Type':'application/octet-stream' if binary else 'application/json'}
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=120) as response:
            return json.load(response)
    body = Path('RELEASE-0.3.0.md').read_text(encoding='utf-8')
    try:
        release = request('GET', base+'/releases/tags/'+tag)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        release = request('POST', base+'/releases', {'tag_name':tag,'target_commitish':commit,
                          'name':'Aurora Lite v0.3.0 · 紧凑列表与自动转发', 'body':body, 'draft':True, 'prerelease':False})
    if not release['draft']:
        raise RuntimeError('Release already published; refusing to replace its assets')
    assets = {asset['name']:asset for asset in request('GET', release['assets_url'])}
    for path in sorted(output.iterdir()):
        if not path.is_file():
            continue
        if path.name in assets:
            asset = assets[path.name]
            if asset.get('digest') == 'sha256:'+hashlib.sha256(path.read_bytes()).hexdigest():
                continue
            raise RuntimeError('Draft already has a different asset: '+path.name)
        url = release['upload_url'].split('{',1)[0]+'?name='+urllib.parse.quote(path.name)
        uploaded = request('POST', url, path.read_bytes(), binary=True)
        if uploaded['size'] != path.stat().st_size:
            raise RuntimeError('Asset size mismatch: '+path.name)
        print('Uploaded '+path.name)
    release = request('PATCH', release['url'], {'body':body,'draft':False,'make_latest':'true'})
    print('Published '+release['html_url'])


if __name__ == '__main__':
    main()
