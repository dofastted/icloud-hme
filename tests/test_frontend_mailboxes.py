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


def test_mailbox_list_shows_created_time_and_sorts_via_query():
    script = r'''
const fs = require('fs');
const vm = require('vm');
let rendered = '';
let requestedPath = '';
const elements = {
  mailboxQ: {value: '', addEventListener(){}},
  mailboxAccount: {value: '', addEventListener(){}},
  mailboxGroup: {value: '', addEventListener(){}},
  mailboxStatus: {value: '', addEventListener(){}},
  mailboxSort: {value: 'created_at', addEventListener(){}},
};
const S = {
  accounts: [{id:'acc_1', name:'Main'}],
  groups: [{id:'grp_default', name:'可用', color:'#111'}],
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
  toast(){},
  async api(path){
    if (path === '/api/mail-probe/config') return {ok:true,config:{enabled:false,interval_minutes:30,start_time:'08:00'},state:{}};
    requestedPath = path;
    if (!path.startsWith('/api/mailboxes?')) throw new Error('unexpected api ' + path);
    return {
      ok:true,
      total:1,
      source:'local',
      mailboxes:[{
        alias_email:'alias@icloud.com',
        account_id:'acc_1',
        account_name:'Main',
        group_id:'grp_default',
        group_name:'可用',
        group_color:'#111',
        label:'Login',
        created_at:1783932943138,
        is_active:true,
        shared:null
      }]
    };
  }
};
const context = {
  window: {HME: S},
  location: {hash:'#/mailboxes'},
  URLSearchParams,
  encodeURIComponent,
  navigator: {clipboard: {writeText(){}}},
  Date,
  Number,
  console,
};
vm.runInNewContext(fs.readFileSync('static/js/03-mailboxes.js', 'utf8'), context);
(async () => {
  await S.renderMailboxes(false);
  if (!requestedPath.includes('sort=created_at')) {
    throw new Error('mailbox list did not query with sort: ' + requestedPath);
  }
  if (!rendered.includes('创建时间') || !rendered.includes('时间新→旧')) {
    throw new Error('mailbox list missing time sort controls: ' + rendered);
  }
  if (!rendered.includes('alias@icloud.com')) {
    throw new Error('mailbox row missing: ' + rendered);
  }
  if (!/\d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(rendered)) {
    throw new Error('created time was not formatted: ' + rendered);
  }
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    run_node(script)


def test_mailbox_list_apply_filters_keeps_non_default_sort():
    script = r'''
const fs = require('fs');
const vm = require('vm');
let navigated = '';
const listeners = {};
function el(value){
  return {
    value,
    addEventListener(type, fn){ listeners[type] = fn; },
  };
}
const elements = {
  mailboxQ: el(''),
  mailboxAccount: el(''),
  mailboxGroup: el(''),
  mailboxStatus: el(''),
  mailboxSort: el('created_at_asc'),
};
const S = {
  accounts: [],
  groups: [],
  mailboxes: [],
  E(id){ return elements[id] || null; },
  esc(value){ return String(value == null ? '' : value); },
  inlineArg(value){ return JSON.stringify(value); },
  setTitle(){},
  loading(){ return 'loading'; },
  empty(){ return 'empty'; },
  error(err){ return String(err.message || err); },
  view(){},
  navigate(hash){ navigated = hash; },
  debounce(_key, fn){ fn(); },
  async api(){ return {ok:true,total:0,mailboxes:[]}; }
};
const context = {
  window: {HME: S},
  location: {hash:'#/mailboxes?sort=created_at_asc'},
  URLSearchParams,
  encodeURIComponent,
  navigator: {clipboard: {writeText(){}}},
  console,
};
vm.runInNewContext(fs.readFileSync('static/js/03-mailboxes.js', 'utf8'), context);
(async () => {
  await S.renderMailboxes(false);
  if (!listeners.change) throw new Error('sort change listener missing');
  listeners.change();
  if (navigated !== '#/mailboxes?sort=created_at_asc') {
    throw new Error('sort was not kept in hash: ' + navigated);
  }
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    run_node(script)

def test_mailbox_list_renders_latest_subject_and_delete_action():
    script = r'''
const fs = require('fs');
const vm = require('vm');
let rendered = '';
const S = {
  accounts: [], groups: [], mailboxes: [],
  E(){ return null; },
  esc(value){ return String(value == null ? '' : value); },
  inlineArg(value){ return JSON.stringify(value); },
  setTitle(){}, loading(){ return ''; }, empty(text){ return text; }, error(err){ return String(err); },
  view(html){ rendered = html; }, debounce(){},
  async api(path){
    if (path.indexOf('/api/mailboxes?') === 0) return {ok:true,total:1,mailboxes:[{alias_email:'a@icloud.com', account_name:'Main', group_id:'grp_default', group_name:'可用', latest_subject:'登录提醒', is_active:true}]};
    return {ok:true,config:{enabled:false,interval_minutes:30,start_time:'08:00'},state:{}};
  }
};
const context = {window:{HME:S}, location:{hash:'#/mailboxes'}, URLSearchParams, encodeURIComponent, console};
vm.runInNewContext(fs.readFileSync('static/js/03-mailboxes.js', 'utf8'), context);
(async () => {
  await S.renderMailboxes(false);
  if (!rendered.includes('最新邮件') || !rendered.includes('登录提醒')) throw new Error('latest subject missing');
  if (rendered.includes('<th>标签</th>')) throw new Error('label column remains');
  if (!rendered.includes('HME.deleteMailbox')) throw new Error('delete action missing');
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''

def test_mailbox_list_renders_provider_badges_and_kind_filter():
    script = r'''
const fs = require('fs'); const vm = require('vm'); let rendered = ''; let requested = '';
const elements = {mailboxQ:{value:'',addEventListener(){}},mailboxAccount:{value:'',addEventListener(){}},mailboxGroup:{value:'',addEventListener(){}},mailboxStatus:{value:'',addEventListener(){}},mailboxKind:{value:'claude',addEventListener(){}},mailboxSort:{value:'created_at',addEventListener(){}}};
const S = {accounts:[],groups:[],mailboxes:[],E(id){return elements[id]||null},esc(v){return String(v??'')},inlineArg(v){return JSON.stringify(v)},setTitle(){},empty(v){return v},view(h){rendered=h},debounce(){},async api(path){requested=path;return {ok:true,total:1,mailboxes:[{alias_email:'a@icloud.com',account_name:'Main',group_name:'可用',latest_subject:'Hello',has_claude:true,has_openai:true,is_active:true}]}}};
const context={window:{HME:S},location:{hash:'#/mailboxes?mail_kind=claude'},URLSearchParams,encodeURIComponent,console}; vm.runInNewContext(fs.readFileSync('static/js/03-mailboxes.js','utf8'),context);
(async()=>{await S.renderMailboxes(false); if(!requested.includes('mail_kind=claude'))throw Error('kind query missing: '+requested); if(!rendered.includes('Claude')||!rendered.includes('OpenAI'))throw Error('provider badges missing: '+rendered); if(!rendered.includes('空邮箱'))throw Error('empty filter missing');})().catch(e=>{console.error(e.stack);process.exit(1)});
'''
    run_node(script)


def test_mailbox_list_paginates_and_renders_copy_button():
    script = r'''
const fs = require('fs');
const vm = require('vm');
let rendered = '';
let requested = '';
let navigated = '';
const rows = Array.from({length:20}).map((_, i) => ({alias_email:'a' + i + '@icloud.com', account_name:'Main', group_name:'可用', is_active:true}));
const S = {
  accounts: [], groups: [], mailboxes: [],
  E(){ return null; },
  esc(v){ return String(v == null ? '' : v); },
  inlineArg(v){ return JSON.stringify(v); },
  setTitle(){}, empty(v){ return v; }, error(e){ return String(e); },
  view(h){ rendered = h; }, debounce(){}, navigate(h){ navigated = h; }, toast(){},
  async api(path){ requested = path; return {ok:true, total:643, limit:20, offset:40, mailboxes:rows}; }
};
const context = {window:{HME:S}, location:{hash:'#/mailboxes?page=3&limit=20'}, URLSearchParams, encodeURIComponent, console};
vm.runInNewContext(fs.readFileSync('static/js/03-mailboxes.js', 'utf8'), context);
(async () => {
  await S.renderMailboxes(false);
  if (!requested.includes('limit=20') || !requested.includes('offset=40')) throw new Error('page query missing: ' + requested);
  if (!rendered.includes('第 3 / 33 页') || !rendered.includes('共 643 个')) throw new Error('pager missing: ' + rendered);
  if (!rendered.includes('>41<')) throw new Error('row numbering ignores offset: ' + rendered);
  if (!rendered.includes('HME.copyText')) throw new Error('copy button missing');
  S.gotoMailboxPage(4);
  if (navigated !== '#/mailboxes?page=4&limit=20') throw new Error('page navigation wrong: ' + navigated);
  S.setMailboxPageSize(50);
  if (navigated !== '#/mailboxes') throw new Error('page size reset wrong: ' + navigated);
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    run_node(script)


def test_mailbox_delete_falls_back_to_local_only_cleanup():
    script = r'''
const fs = require('fs');
const vm = require('vm');
const calls = [];
const toasts = [];
const S = {
  accounts: [], groups: [], mailboxes: [],
  E(){ return null; },
  esc(v){ return String(v == null ? '' : v); },
  inlineArg(v){ return JSON.stringify(v); },
  setTitle(){}, empty(v){ return v; }, error(e){ return String(e); }, view(){}, debounce(){},
  toast(msg){ toasts.push(msg); },
  async refreshAll(){},
  async api(path, opts){
    calls.push(path);
    if (!path.includes('local_only=1')) {
      const err = new Error('无法从 Apple 获取该别名标识');
      err.code = 'alias_id_unresolved';
      throw err;
    }
    return {ok:true, mailbox:{warning:'按请求仅清理本地记录，Apple 别名保留'}};
  }
};
const context = {window:{HME:S}, location:{hash:'#/mailboxes'}, URLSearchParams, encodeURIComponent, console, confirm: () => true};
vm.runInNewContext(fs.readFileSync('static/js/03-mailboxes.js', 'utf8'), context);
(async () => {
  await S.deleteMailbox('a@icloud.com');
  if (calls.length !== 2 || !calls[1].includes('local_only=1')) throw new Error('local-only retry missing: ' + JSON.stringify(calls));
  if (!toasts.some(t => t.includes('仅清理本地记录'))) throw new Error('warning toast missing: ' + JSON.stringify(toasts));
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    run_node(script)
