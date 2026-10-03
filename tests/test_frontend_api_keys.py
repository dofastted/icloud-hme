import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_api_key_page_create_store_and_list_flow():
    script = r"""
const fs = require('fs');
const vm = require('vm');
const storage = {};
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
    if (path === '/api/keys' && opts.method === 'POST') {
      return {ok:true, status:200, json:async () => ({ok:true, key:{id:'key_1', name:'integration', prefix:'hme_created', active:true, api_key:'hme_created_secret'}})};
    }
    if (path === '/api/keys') {
      return {ok:true, status:200, json:async () => ({ok:true, keys:[{id:'key_1', name:'integration', prefix:'hme_created', active:true}]})};
    }
    throw new Error('unexpected fetch ' + path);
  },
  console,
};
vm.runInNewContext(fs.readFileSync('static/js/06-api-keys.js', 'utf8'), context);
(async () => {
  await S.createApiKey();
  if (storage.icloud_hme_api_key !== 'hme_created_secret') throw new Error('created key was not stored');
  if (elements.apiKeyInput.value !== 'hme_created_secret') throw new Error('created key was not placed in input');
  if (!elements.apiKeyRows.innerHTML.includes('key_1')) throw new Error('key list was not rendered');
  S.clearApiKeyInput();
  if ('icloud_hme_api_key' in storage) throw new Error('cleared key remains in browser storage');
  if (elements.apiKeyInput.value !== '') throw new Error('cleared key remains in input');
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
"""
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
