import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_api_key_nav_and_route_are_wired():
    template = (ROOT / "templates/index.html").read_text(encoding="utf-8")
    core = (ROOT / "static/js/01-core.js").read_text(encoding="utf-8")
    docs = (ROOT / "static/js/05-inbox-docs.js").read_text(encoding="utf-8")

    assert 'data-route="#/api-keys"' in template
    assert "js/06-api-keys.js" in template
    assert "renderApiKeys" in core
    assert "#/api-keys" in docs


def test_api_key_page_create_store_and_list_flow():
    script = r'''
const fs = require('fs');
const vm = require('vm');
let rendered = '';
let copied = '';
const storage = {};
const calls = [];
const elements = {
  apiKeyInput: {value: ''},
  apiKeyNameInput: {value: 'integration'},
  apiKeyMsg: {innerHTML: '', textContent: ''},
  apiKeyRows: {innerHTML: ''},
};
const S = {
  E(id){ return elements[id]; },
  esc(value){ return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
  inlineArg(value){ return this.esc(JSON.stringify(value)); },
  setTitle(title){ this.title = title; },
  view(html){ rendered = html; },
  toast(msg, err){ this.lastToast = {msg, err: !!err}; },
  error(err){ return '<div class="error-box">' + this.esc(err.message || err) + '</div>'; },
};
const context = {
  window: {HME: S},
  localStorage: {
    getItem(key){ return storage[key] || ''; },
    setItem(key, value){ storage[key] = String(value); },
    removeItem(key){ delete storage[key]; },
  },
  fetch: async (path, opts = {}) => {
    calls.push({path, opts});
    if (path === '/api/keys' && opts.method === 'POST') {
      return {ok:true, status:200, json:async () => ({ok:true, key:{id:'key_1', name:'integration', prefix:'hme_created', active:true, api_key:'hme_created_secret'}})};
    }
    if (path === '/api/keys') {
      return {ok:true, status:200, json:async () => ({ok:true, keys:[{id:'key_1', name:'integration', prefix:'hme_created', active:true}]})};
    }
    if (path === '/api/keys/key_1/revoke') {
      return {ok:true, status:200, json:async () => ({ok:true})};
    }
    throw new Error('unexpected fetch ' + path);
  },
  navigator: {clipboard: {writeText(value){ copied = value; }}},
  console,
};
vm.runInNewContext(fs.readFileSync('static/js/06-api-keys.js', 'utf8'), context);
(async () => {
  await S.renderApiKeys();
  if (!rendered.includes('API Key 管理') || !rendered.includes('/api/v1/*')) throw new Error('API Key page did not render expected help');
  await S.createApiKey();
  if (storage.icloud_hme_api_key !== 'hme_created_secret') throw new Error('created key was not stored');
  if (elements.apiKeyInput.value !== 'hme_created_secret') throw new Error('created key was not placed in input');
  if (!elements.apiKeyMsg.innerHTML.includes('明文只显示这一次')) throw new Error('one-time plaintext warning missing');
  if (!elements.apiKeyRows.innerHTML.includes('key_1')) throw new Error('key list was not rendered');
  const listCall = calls.find(call => call.path === '/api/keys' && !call.opts.method);
  if (!listCall || listCall.opts.headers.Authorization !== 'Bearer hme_created_secret') throw new Error('list call missing auth header');
  await S.revokeApiKey('key_1');
  const revokeCall = calls.find(call => call.path === '/api/keys/key_1/revoke');
  if (!revokeCall || revokeCall.opts.headers.Authorization !== 'Bearer hme_created_secret') throw new Error('revoke call missing auth header');
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
