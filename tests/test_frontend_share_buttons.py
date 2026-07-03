import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mailbox_share_buttons_escape_inline_arguments():
    script = r'''
const fs = require('fs');
const vm = require('vm');
let rendered = '';
const elements = {
  mailboxQ: null,
  mailboxAccount: null,
  mailboxStatus: null,
};
const S = {
  accounts: [{id: 'acc_1', name: 'Main'}],
  mailboxes: [],
  E(id){ return elements[id] || null; },
  esc(value){ return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
  inlineArg(value){ return this.esc(JSON.stringify(value)); },
  setTitle(){},
  loading(){ return 'loading'; },
  empty(){ return 'empty'; },
  error(err){ return String(err.message || err); },
  view(html){ rendered = html; },
  navigate(){},
  debounce(){},
  async api(path){
    if (!path.startsWith('/api/mailboxes?')) throw new Error('unexpected api ' + path);
    return {ok:true,total:1,mailboxes:[{alias_email:'alias@icloud.com', account_id:'acc_1', account_name:'Main', label:'', is_active:true, shared:null}]};
  }
};
const context = {
  window: {HME: S},
  location: {hash:'#/mailboxes?account=acc_1'},
  URLSearchParams,
  encodeURIComponent,
  navigator: {clipboard: {writeText(){}}},
  console,
};
vm.runInNewContext(fs.readFileSync('static/js/03-mailboxes.js', 'utf8'), context);
(async () => {
  await S.renderMailboxes(false);
  const marker = 'onclick="HME.createShare(&quot;alias@icloud.com&quot;)"';
  if (!rendered.includes(marker)) {
    throw new Error('share onclick is not HTML-escaped: ' + rendered);
  }
  if (rendered.includes('onclick="HME.createShare("alias@icloud.com")"')) {
    throw new Error('share onclick still contains raw quotes');
  }
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_share_modal_confirm_button_escapes_inline_argument():
    script = r'''
const fs = require('fs');
const vm = require('vm');
const elements = {modalRoot: {innerHTML: ''}};
const S = {
  E(id){ return elements[id] || null; },
  esc(value){ return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
  inlineArg(value){ return this.esc(JSON.stringify(value)); },
  closeModal(){ elements.modalRoot.innerHTML = ''; },
  async api(){ throw new Error('not used'); },
  toast(){},
  routeView(){},
};
const context = {window: {HME: S}, console, encodeURIComponent, navigator: {clipboard: {writeText(){}}}};
vm.runInNewContext(fs.readFileSync('static/js/04-shared.js', 'utf8'), context);
S.createShare('alias@icloud.com');
const html = elements.modalRoot.innerHTML;
const marker = 'onclick="HME.confirmCreateShare(&quot;alias@icloud.com&quot;)"';
if (!html.includes(marker)) {
  throw new Error('modal confirm onclick is not HTML-escaped: ' + html);
}
if (html.includes('onclick="HME.confirmCreateShare("alias@icloud.com")"')) {
  throw new Error('modal confirm onclick still contains raw quotes');
}
'''
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_share_modal_shows_entry_and_redemption_code():
    script = r'''
const fs = require('fs');
const vm = require('vm');
const elements = {modalRoot: {innerHTML: ''}};
const S = {
  E(id){ return elements[id] || null; },
  esc(value){ return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
  inlineArg(value){ return this.esc(JSON.stringify(value)); },
  closeModal(){ elements.modalRoot.innerHTML = ''; },
  async api(path){
    if (path !== '/api/mailboxes/alias%40icloud.com/share') throw new Error('unexpected api ' + path);
    return {ok:true, share_url:'https://shared.example.com/shared', redemption_code:'shk_code_123'};
  },
  toast(){},
  routeView(){},
};
const context = {window: {HME: S}, console, encodeURIComponent, navigator: {clipboard: {writeText(){}}}};
vm.runInNewContext(fs.readFileSync('static/js/04-shared.js', 'utf8'), context);
(async () => {
  await S.confirmCreateShare('alias@icloud.com');
  const html = elements.modalRoot.innerHTML;
  if (!html.includes('https://shared.example.com/shared')) throw new Error('missing shared entry URL');
  if (!html.includes('shk_code_123')) throw new Error('missing redemption code');
  if (!html.includes('复制兑换码')) throw new Error('missing copy redemption button');
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
