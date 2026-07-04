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


def test_account_group_select_options_are_html_escaped_in_add_and_edit_modals():
    script = r'''
const fs = require('fs');
const vm = require('vm');
const maliciousName = '<img src=x onerror=alert(1)> Group & "Team"';
const maliciousId = 'grp_bad" autofocus onfocus="alert(2)';
const elements = {
  modalRoot: {innerHTML: ''},
};
const S = {
  groups: [
    {id: 'grp_default', name: '默认分组', color: '#666666', is_default: true},
    {id: maliciousId, name: maliciousName, color: '#123456'},
  ],
  E(id){
    if (!elements[id]) elements[id] = {value: '', innerHTML: '', textContent: '', disabled: false};
    return elements[id];
  },
  esc(value){ return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
  inlineArg(value){ return this.esc(JSON.stringify(value)); },
  async api(path){
    if (path === '/api/accounts/acc_1/session') {
      return {ok:true, account:{id:'acc_1', name:'Main', host:'icloud.com', cookie_input:'A=1', group_id: maliciousId}};
    }
    throw new Error('unexpected api ' + path);
  },
  toast(msg){ throw new Error(msg); },
};
const context = {window: {HME: S}, encodeURIComponent, confirm(){ return true; }, console};
vm.runInNewContext(fs.readFileSync('static/js/02-accounts.js', 'utf8'), context);
(async () => {
  S.showAddAccountModal();
  const addHtml = elements.modalRoot.innerHTML;
  if (!addHtml.includes('&lt;img src=x onerror=alert(1)&gt; Group &amp; &quot;Team&quot;')) {
    throw new Error('add modal group option text is not escaped: ' + addHtml);
  }
  if (addHtml.includes(maliciousName) || addHtml.includes('value="grp_bad" autofocus')) {
    throw new Error('add modal contains raw group option HTML: ' + addHtml);
  }

  await S.showEditAccountModal('acc_1');
  const editHtml = elements.modalRoot.innerHTML;
  if (!editHtml.includes('&lt;img src=x onerror=alert(1)&gt; Group &amp; &quot;Team&quot;')) {
    throw new Error('edit modal group option text is not escaped: ' + editHtml);
  }
  if (editHtml.includes(maliciousName) || editHtml.includes('value="grp_bad" autofocus')) {
    throw new Error('edit modal contains raw group option HTML: ' + editHtml);
  }
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    run_node(script)


def test_mailbox_group_filter_options_escape_html_and_query_by_group_id():
    script = r'''
const fs = require('fs');
const vm = require('vm');
let rendered = '';
let requestedPath = '';
const maliciousName = '<svg onload=alert(1)> Work & "Team"';
const elements = {
  mailboxQ: {value: '', addEventListener(){}},
  mailboxGroup: {value: 'grp_bad', addEventListener(){}},
  mailboxStatus: {value: '', addEventListener(){}},
};
const S = {
  accounts: [],
  groups: [
    {id: 'grp_bad', name: maliciousName, color: '#123456'},
    {id: 'grp_safe', name: 'Safe', color: '#654321'},
  ],
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
    requestedPath = path;
    if (!path.startsWith('/api/mailboxes?')) throw new Error('unexpected api ' + path);
    return {ok:true,total:0,mailboxes:[]};
  }
};
const context = {
  window: {HME: S},
  location: {hash:'#/mailboxes?group_id=grp_bad'},
  URLSearchParams,
  encodeURIComponent,
  navigator: {clipboard: {writeText(){}}},
  console,
};
vm.runInNewContext(fs.readFileSync('static/js/03-mailboxes.js', 'utf8'), context);
(async () => {
  await S.renderMailboxes(false);
  if (!requestedPath.includes('group_id=grp_bad')) {
    throw new Error('mailbox list did not query with group_id: ' + requestedPath);
  }
  if (!rendered.includes('&lt;svg onload=alert(1)&gt; Work &amp; &quot;Team&quot;')) {
    throw new Error('mailbox group option text is not escaped: ' + rendered);
  }
  if (rendered.includes(maliciousName)) {
    throw new Error('mailbox filter contains raw group option HTML: ' + rendered);
  }
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    run_node(script)
