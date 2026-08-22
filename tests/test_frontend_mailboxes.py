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
