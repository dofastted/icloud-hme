import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_mailbox_detail_degrades_when_mail_login_fails():
    script = r'''
const fs = require('fs');
const vm = require('vm');
let rendered = '';
let toast = null;
const S = {
  accounts: [],
  mailboxes: [],
  E(){ return {textContent: ''}; },
  esc(value){ return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
  inlineArg(value){ return this.esc(JSON.stringify(value)); },
  setTitle(){},
  loading(){ return 'loading'; },
  empty(text){ return '<div class="empty">' + this.esc(text) + '</div>'; },
  error(err){ return '<div class="error-box">' + this.esc(err.message || err) + '</div>'; },
  view(html){ rendered = html; },
  navigate(){},
  toast(msg, err){ toast = {msg, err: !!err}; },
  debounce(){},
  async api(path){
    if (path === '/api/mailboxes/alias%40icloud.com') {
      return {ok:true, mailbox:{alias_email:'alias@icloud.com', account_id:'acc_1', account_name:'Main', label:'Login', is_active:true, shared:null}};
    }
    if (path === '/api/mailboxes/alias%40icloud.com/messages?limit=1') {
      throw new Error('邮件登录失败 — 请检查邮件认证凭据和账号状态');
    }
    throw new Error('unexpected api ' + path);
  }
};
const context = {window: {HME: S}, location: {hash:'#/mailbox/alias%40icloud.com'}, URLSearchParams, encodeURIComponent, navigator: {clipboard: {writeText(){}}}, console};
vm.runInNewContext(fs.readFileSync('static/js/03-mailboxes.js', 'utf8'), context);
(async () => {
  await S.renderMailboxDetail('alias@icloud.com');
  if (!rendered.includes('alias@icloud.com')) throw new Error('mailbox detail did not render');
  if (!rendered.includes('邮件读取暂不可用')) throw new Error('sanitized mail error missing: ' + rendered);
  if (rendered.includes('邮件登录失败') || rendered.includes('邮件认证凭据')) throw new Error('raw login error leaked: ' + rendered);
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
