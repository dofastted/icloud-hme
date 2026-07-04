import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_inbox_imap_test_button_posts_selected_account():
    script = r'''
const fs = require('fs');
const vm = require('vm');
const elements = {
  inboxAccount: {value: 'acc_1'},
  inboxAlias: {value: 'alias@icloud.com'},
  inboxResult: {innerHTML: ''},
};
let rendered = '';
let posted = null;
const S = {
  accounts: [{id: 'acc_1', name: 'Main'}],
  E(id){ return elements[id] || null; },
  esc(value){ return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
  setTitle(){},
  empty(text){ return '<div class="empty">' + this.esc(text) + '</div>'; },
  loading(){ return '<div>loading</div>'; },
  error(err){ return '<div class="error">' + this.esc(err.message || err) + '</div>'; },
  view(html){ rendered = html; },
  async api(){ throw new Error('not used'); },
};
const context = {
  window: {HME: S},
  encodeURIComponent,
  console,
  fetch: async (path, opts) => {
    posted = {path, body: JSON.parse(opts.body)};
    return {ok: true, json: async () => ({ok:true, mail:{ok:true, email:'user@qq.com', server:'imap.qq.com', port:993, inbox_count:3, recent_count:1, days:30, messages:[{subject:'Hi', from:'a@example.com', to:'alias@icloud.com', date:'today'}]}})};
  },
};
vm.runInNewContext(fs.readFileSync('static/js/05-inbox-docs.js', 'utf8'), context);
(async () => {
  S.renderInbox();
  if (!rendered.includes('测试 IMAP')) throw new Error('IMAP test button missing: ' + rendered);
  await S.testInboxImap();
  if (posted.path !== '/api/accounts/acc_1/mail-settings/test') throw new Error('wrong path ' + posted.path);
  if (posted.body.alias !== 'alias@icloud.com' || posted.body.days !== 30) throw new Error('wrong payload ' + JSON.stringify(posted.body));
  if (!elements.inboxResult.innerHTML.includes('IMAP 测试') || !elements.inboxResult.innerHTML.includes('imap.qq.com')) {
    throw new Error('IMAP result not rendered: ' + elements.inboxResult.innerHTML);
  }
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr


def test_log_stream_appends_line_without_rerendering_page():
    script = r'''
const fs = require('fs');
const vm = require('vm');
const elements = {
  logFeed: {
    innerHTML: '',
    scrollTop: 0,
    scrollHeight: 0,
    insertAdjacentHTML(_position, html){ this.innerHTML += html; }
  }
};
let rendered = '';
let viewCalls = 0;
class FakeEventSource {
  constructor(url){ this.url = url; FakeEventSource.instance = this; }
  close(){}
}
const S = {
  accounts: [],
  logs: [{time:'10:00:00', level:'info', msg:'old line'}],
  E(id){ return elements[id] || null; },
  esc(value){ return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
  setTitle(){},
  empty(text){ return '<div class="empty">' + this.esc(text) + '</div>'; },
  view(html){
    rendered = html;
    viewCalls += 1;
    const match = html.match(/<div class="panel-body mono" id="logFeed">([\s\S]*)<\/div><\/div>$/);
    if (match) elements.logFeed.innerHTML = match[1];
  },
};
const context = {
  window: {HME: S},
  location: {hash:'#/logs'},
  EventSource: FakeEventSource,
  setTimeout(){},
  console,
};
vm.runInNewContext(fs.readFileSync('static/js/05-inbox-docs.js', 'utf8'), context);
S.renderLogs();
if (viewCalls !== 1) throw new Error('initial logs should render once');
FakeEventSource.instance.onmessage({data: JSON.stringify({time:'10:00:01', level:'warn', msg:'new line'})});
if (viewCalls !== 1) throw new Error('SSE log update rerendered the whole log page');
if (!elements.logFeed.innerHTML.includes('old line') || !elements.logFeed.innerHTML.includes('new line')) {
  throw new Error('log feed did not preserve old line and append new line: ' + elements.logFeed.innerHTML + ' / ' + rendered);
}
'''
    result = subprocess.run(["node", "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=10, check=False)
    assert result.returncode == 0, result.stderr
