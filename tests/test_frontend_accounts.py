import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run_node(script: str):
    result = subprocess.run(
        ["node", "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_dashboard_account_card_has_delete_button():
    script = r'''
const fs = require('fs');
const vm = require('vm');
let rendered = '';
const S = {
  accounts: [{id:'acc_1', name:'Main', real_email:'main@example.com', status:'active', alias_total:2, alias_active:1}],
  state: {account_count:1, active_accounts:1, total_aliases:2, today_created:0},
  E(){ return null; },
  esc(value){ return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
  inlineArg(value){ return this.esc(JSON.stringify(value)); },
  setTitle(){},
  view(html){ rendered = html; },
};
const context = {window: {HME: S}, encodeURIComponent, confirm(){ return true; }, console};
vm.runInNewContext(fs.readFileSync('static/js/02-accounts.js', 'utf8'), context);
(async () => {
  await S.renderDashboard();
  const marker = 'onclick="HME.removeAccount(&quot;acc_1&quot;,&quot;Main&quot;)"';
  if (!rendered.includes(marker)) throw new Error('delete account button missing: ' + rendered);
  const editMarker = 'onclick="HME.showEditAccountModal(&quot;acc_1&quot;)"';
  if (!rendered.includes(editMarker)) throw new Error('edit account button missing: ' + rendered);
  if (!rendered.includes('btn-danger') || !rendered.includes('>删除</button>')) throw new Error('delete button style/text missing: ' + rendered);
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    run_node(script)


def test_add_account_ignores_second_click_while_pending():
    script = r'''
const fs = require('fs');
const vm = require('vm');
const elements = {
  accNameInput: {value: 'Main'},
  cookieInput: {value: 'A=B'},
  addAccountSubmit: {disabled: false, textContent: '添加并校验'},
  modalMsg: {textContent: ''},
  modalRoot: {innerHTML: 'open'},
};
let apiCalls = 0;
let finishApi;
let toast = '';
let refreshCalls = 0;
const S = {
  E(id){ return elements[id] || null; },
  esc(value){ return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
  inlineArg(value){ return this.esc(JSON.stringify(value)); },
  async api(path){
    if (path !== '/api/accounts/add') throw new Error('unexpected api ' + path);
    apiCalls += 1;
    return new Promise(resolve => { finishApi = () => resolve({ok:true}); });
  },
  toast(msg){ toast = msg; },
  refreshAll(){ refreshCalls += 1; },
};
const context = {window: {HME: S}, encodeURIComponent, confirm(){ return true; }, console};
vm.runInNewContext(fs.readFileSync('static/js/02-accounts.js', 'utf8'), context);
(async () => {
  const first = S.addAccount();
  if (apiCalls !== 1) throw new Error('first click did not submit');
  if (!elements.addAccountSubmit.disabled) throw new Error('submit button not disabled during add');
  if (elements.addAccountSubmit.textContent !== '添加中...') throw new Error('busy label missing');
  const second = S.addAccount();
  if (apiCalls !== 1) throw new Error('second click submitted again');
  finishApi();
  await Promise.all([first, second]);
  if (toast !== '账号已添加') throw new Error('success toast missing');
  if (refreshCalls !== 1) throw new Error('refresh should run once, got ' + refreshCalls);
  if (elements.modalRoot.innerHTML !== '') throw new Error('modal was not closed');
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    run_node(script)


def test_account_edit_modal_reads_and_updates_session():
    script = r'''
const fs = require('fs');
const vm = require('vm');
const elements = {
  modalRoot: {innerHTML: ''},
  editAccountNameInput: {value: 'Renamed'},
  editAccountHostInput: {value: 'icloud.com.cn'},
  editAccountSessionInput: {value: 'C=3'},
  editAccountSubmit: {disabled: false, textContent: '保存并校验'},
  modalMsg: {textContent: ''},
};
let posted = null;
let toast = '';
let refreshCalls = 0;
const S = {
  E(id){ return elements[id] || null; },
  esc(value){ return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
  inlineArg(value){ return this.esc(JSON.stringify(value)); },
  async api(path, opts){
    if (path === '/api/accounts/acc_1/session' && !opts) {
      return {ok:true, account:{id:'acc_1', name:'Main', host:'icloud.com', cookie_input:'A=1; B=2'}};
    }
    if (path === '/api/accounts/acc_1/session' && opts && opts.method === 'POST') {
      posted = JSON.parse(opts.body);
      return {ok:true, account:{id:'acc_1', name:posted.name, status:'active'}};
    }
    throw new Error('unexpected api ' + path);
  },
  toast(msg){ toast = msg; },
  refreshAll(){ refreshCalls += 1; },
};
const context = {window: {HME: S}, encodeURIComponent, confirm(){ return true; }, console};
vm.runInNewContext(fs.readFileSync('static/js/02-accounts.js', 'utf8'), context);
(async () => {
  await S.showEditAccountModal('acc_1');
  const html = elements.modalRoot.innerHTML;
  if (!html.includes('A=1; B=2')) throw new Error('session data missing from modal: ' + html);
  if (!html.includes('编辑账号 Session')) throw new Error('edit modal title missing: ' + html);
  await S.saveAccountSession('acc_1');
  if (!elements.editAccountSubmit.disabled) throw new Error('submit button was not disabled');
  if (posted.name !== 'Renamed' || posted.host !== 'icloud.com.cn' || posted.cookie_input !== 'C=3') {
    throw new Error('unexpected update payload ' + JSON.stringify(posted));
  }
  if (toast !== '账号已更新') throw new Error('success toast missing');
  if (refreshCalls !== 1) throw new Error('refresh should run once, got ' + refreshCalls);
  if (elements.modalRoot.innerHTML !== '') throw new Error('modal was not closed');
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    run_node(script)
