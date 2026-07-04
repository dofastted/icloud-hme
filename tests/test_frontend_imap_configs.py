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


def test_dashboard_mail_modal_creates_imap_config_then_binds_account():
    script = r'''
const fs = require('fs');
const vm = require('vm');
const elements = {
  modalRoot: {innerHTML: ''},
  mailConfigSelect: {value: ''},
  mailConfigNameInput: {value: 'QQ 收件箱', disabled: false},
  mailEmailInput: {value: 'user@qq.com', disabled: false},
  mailHostInput: {value: '', disabled: false},
  mailPortInput: {value: '993', disabled: false},
  mailPasswordInput: {value: 'auth-code', disabled: false, placeholder: ''},
  modalMsg: {textContent: ''},
};
const calls = [];
let toast = '';
let refreshCalls = 0;
const S = {
  accounts: [{id:'acc_1', name:'Main', real_email:'user@qq.com', mail_port:993}],
  imapConfigs: [],
  E(id){ return elements[id] || null; },
  esc(value){ return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
  inlineArg(value){ return this.esc(JSON.stringify(value)); },
  navigate(){},
  async api(path, opts){
    calls.push({path, body: opts && opts.body ? JSON.parse(opts.body) : null});
    if (path === '/api/imap-configs') return {ok:true, config:{id:'imap_1'}};
    if (path === '/api/accounts/acc_1/mail-settings') return {ok:true, mail:{ok:true}};
    throw new Error('unexpected api ' + path);
  },
  toast(msg){ toast = msg; },
  refreshAll(){ refreshCalls += 1; },
};
const context = {window: {HME: S}, encodeURIComponent, confirm(){ return true; }, console};
vm.runInNewContext(fs.readFileSync('static/js/02-accounts.js', 'utf8'), context);
(async () => {
  S.showMailSettingsModal('acc_1');
  if (!elements.modalRoot.innerHTML.includes('新建 IMAP 配置')) throw new Error('new config option missing: ' + elements.modalRoot.innerHTML);
  elements.mailConfigNameInput.value = 'QQ 收件箱';
  elements.mailEmailInput.value = 'user@qq.com';
  elements.mailHostInput.value = '';
  elements.mailPortInput.value = '993';
  elements.mailPasswordInput.value = 'auth-code';
  await S.saveMailSettings('acc_1');
  if (calls.length !== 2) throw new Error('expected create and bind calls, got ' + calls.length);
  if (calls[0].path !== '/api/imap-configs') throw new Error('wrong create path ' + calls[0].path);
  if (calls[0].body.name !== 'QQ 收件箱' || calls[0].body.email !== 'user@qq.com' || calls[0].body.password !== 'auth-code') throw new Error('wrong create payload ' + JSON.stringify(calls[0].body));
  if (calls[1].path !== '/api/accounts/acc_1/mail-settings') throw new Error('wrong bind path ' + calls[1].path);
  if (calls[1].body.imap_config_id !== 'imap_1') throw new Error('wrong bind payload ' + JSON.stringify(calls[1].body));
  if (toast !== '邮件登录已通过') throw new Error('toast missing');
  if (refreshCalls !== 1) throw new Error('refresh missing');
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    run_node(script)


def test_dashboard_mail_modal_uses_existing_imap_config_without_password():
    script = r'''
const fs = require('fs');
const vm = require('vm');
const elements = {
  modalRoot: {innerHTML: ''},
  mailConfigSelect: {value: 'imap_1'},
  mailConfigNameInput: {value: '', disabled: false},
  mailEmailInput: {value: '', disabled: false},
  mailHostInput: {value: '', disabled: false},
  mailPortInput: {value: '', disabled: false},
  mailPasswordInput: {value: 'should-clear', disabled: false, placeholder: ''},
  modalMsg: {textContent: ''},
};
const calls = [];
const S = {
  accounts: [{id:'acc_1', name:'Main', imap_config_id:'imap_1', has_mail_config:true}],
  imapConfigs: [{id:'imap_1', name:'QQ 收件箱', email:'user@qq.com', host:'imap.qq.com', port:993}],
  E(id){ return elements[id] || null; },
  esc(value){ return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
  inlineArg(value){ return this.esc(JSON.stringify(value)); },
  navigate(){},
  async api(path, opts){ calls.push({path, body: JSON.parse(opts.body)}); return {ok:true, mail:{ok:true}}; },
  toast(){},
  refreshAll(){},
};
const context = {window: {HME: S}, encodeURIComponent, confirm(){ return true; }, console};
vm.runInNewContext(fs.readFileSync('static/js/02-accounts.js', 'utf8'), context);
(async () => {
  S.showMailSettingsModal('acc_1');
  if (!elements.mailEmailInput.disabled || !elements.mailPasswordInput.disabled) throw new Error('existing config fields should be disabled');
  if (elements.mailPasswordInput.value !== '') throw new Error('password should be cleared');
  await S.saveMailSettings('acc_1');
  if (calls.length !== 1) throw new Error('should only bind existing config');
  if (calls[0].path !== '/api/accounts/acc_1/mail-settings') throw new Error('wrong path ' + calls[0].path);
  if (calls[0].body.imap_config_id !== 'imap_1') throw new Error('wrong payload ' + JSON.stringify(calls[0].body));
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    run_node(script)


def test_imap_config_center_renders_and_saves_config():
    script = r'''
const fs = require('fs');
const vm = require('vm');
let rendered = '';
const elements = {
  modalRoot: {innerHTML: ''},
  imapNameInput: {value: '163 收件箱'},
  imapEmailInput: {value: 'user@163.com'},
  imapHostInput: {value: 'imap.163.com'},
  imapPortInput: {value: '993'},
  imapPasswordInput: {value: 'auth-code'},
  modalMsg: {textContent: ''},
  imapConfigResult: {innerHTML: ''},
};
let saved = null;
const S = {
  accounts: [{id:'acc_1', imap_config_id:'imap_1'}],
  imapConfigs: [],
  E(id){ return elements[id] || null; },
  esc(value){ return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
  inlineArg(value){ return this.esc(JSON.stringify(value)); },
  setTitle(){},
  loading(){ return '<div>loading</div>'; },
  error(err){ return '<div>' + this.esc(err.message || err) + '</div>'; },
  view(html){ rendered = html; },
  async api(path, opts){
    if (path === '/api/imap-configs' && !opts) return {ok:true, configs:[{id:'imap_1', name:'QQ 收件箱', email:'user@qq.com', host:'imap.qq.com', port:993, has_password:true}]};
    if (path === '/api/imap-configs' && opts && opts.method === 'POST') { saved = JSON.parse(opts.body); return {ok:true, config:{id:'imap_2'}}; }
    throw new Error('unexpected api ' + path);
  },
  closeModal(){ elements.modalRoot.innerHTML = ''; },
  toast(){},
  refreshAll(){},
};
const context = {window: {HME: S}, encodeURIComponent, confirm(){ return true; }, console};
vm.runInNewContext(fs.readFileSync('static/js/08-imap-configs.js', 'utf8'), context);
(async () => {
  await S.renderImapConfigs();
  if (!rendered.includes('IMAP 配置中心') || !rendered.includes('QQ 收件箱') || !rendered.includes('已保存密码')) throw new Error('config center render missing: ' + rendered);
  S.showImapConfigModal();
  await S.saveImapConfig('');
  if (!saved || saved.email !== 'user@163.com' || saved.password !== 'auth-code') throw new Error('wrong save payload ' + JSON.stringify(saved));
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    run_node(script)
