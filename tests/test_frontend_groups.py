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


def test_account_modals_do_not_render_group_selectors():
    script = r'''
const fs = require('fs');
const vm = require('vm');
const elements = {
  modalRoot: {innerHTML: ''},
};
const S = {
  E(id){
    if (!elements[id]) elements[id] = {value: '', innerHTML: '', textContent: '', disabled: false};
    return elements[id];
  },
  esc(value){ return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
  inlineArg(value){ return this.esc(JSON.stringify(value)); },
  async api(path){
    if (path === '/api/accounts/acc_1/session') {
      return {ok:true, account:{id:'acc_1', name:'Main', host:'icloud.com', cookie_input:'A=1', group_id:'grp_bad'}};
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
  if (addHtml.includes('所属分组') || addHtml.includes('accGroupInput')) {
    throw new Error('add account modal still contains account group controls: ' + addHtml);
  }

  await S.showEditAccountModal('acc_1');
  const editHtml = elements.modalRoot.innerHTML;
  if (editHtml.includes('所属分组') || editHtml.includes('editAccountGroupInput')) {
    throw new Error('edit account modal still contains account group controls: ' + editHtml);
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


def test_mailbox_group_move_modal_escapes_and_posts_alias_payload():
    script = r'''
const fs = require('fs');
const vm = require('vm');
let requestedPath = '';
let requestedBody = null;
const maliciousName = '<img src=x onerror=alert(1)> Group & "Team"';
const maliciousId = 'grp_bad" autofocus onfocus="alert(2)';
const elements = {
  modalRoot: {innerHTML: ''},
  mailboxGroupTarget: {value: maliciousId},
  modalMsg: {textContent: ''},
};
const S = {
  accounts: [],
  groups: [
    {id: 'grp_default', name: '可用', color: '#1f8b4c', is_default: true, is_system: true},
    {id: maliciousId, name: maliciousName, color: '#123456'},
  ],
  mailboxes: [],
  E(id){ return elements[id] || null; },
  esc(value){ return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
  inlineArg(value){ return this.esc(JSON.stringify(value)); },
  closeModal(){ elements.modalRoot.innerHTML = ''; },
  toast(){},
  async refreshAll(){},
  async api(path, opts){
    requestedPath = path;
    requestedBody = JSON.parse(opts.body);
    return {ok:true,moved:1};
  },
};
const context = {
  window: {HME: S},
  location: {hash:'#/mailboxes'},
  URLSearchParams,
  encodeURIComponent,
  navigator: {clipboard: {writeText(){}}},
  console,
};
vm.runInNewContext(fs.readFileSync('static/js/03-mailboxes.js', 'utf8'), context);
(async () => {
  S.showMailboxGroupModal('alias@icloud.com', maliciousId);
  const html = elements.modalRoot.innerHTML;
  if (!html.includes('&lt;img src=x onerror=alert(1)&gt; Group &amp; &quot;Team&quot;')) {
    throw new Error('move modal group option text is not escaped: ' + html);
  }
  if (html.includes(maliciousName) || html.includes('value="grp_bad" autofocus')) {
    throw new Error('move modal contains raw group HTML: ' + html);
  }

  await S.moveMailboxToGroup('alias@icloud.com');
  if (requestedPath !== '/api/mailboxes/batch-update-group') {
    throw new Error('wrong move endpoint: ' + requestedPath);
  }
  if (requestedBody.group_id !== maliciousId || requestedBody.alias_emails[0] !== 'alias@icloud.com') {
    throw new Error('wrong move payload: ' + JSON.stringify(requestedBody));
  }
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    run_node(script)

def test_group_page_marks_system_status_groups_readonly():
    script = r'''
const fs = require('fs');
const vm = require('vm');
let rendered = '';
const S = {
  groups: [],
  E(){ return {innerHTML: ''}; },
  esc(value){ return String(value == null ? '' : value).replace(/[&<>"']/g, ch => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch])); },
  inlineArg(value){ return this.esc(JSON.stringify(value)); },
  setTitle(){},
  loading(){ return 'loading'; },
  error(err){ return String(err.message || err); },
  view(html){ rendered = html; },
  async api(path){
    if (path !== '/api/groups') throw new Error('unexpected api ' + path);
    return {ok:true, groups:[
      {id:'grp_default', name:'可用', color:'#1f8b4c', is_default:true, is_system:true, mailbox_count:1},
      {id:'grp_unavailable', name:'不可用', color:'#d97706', is_system:true, mailbox_count:2},
      {id:'grp_deprecated', name:'废弃', color:'#6b7280', is_system:true, mailbox_count:3},
      {id:'grp_custom', name:'Custom', color:'#123456', mailbox_count:0},
    ]};
  },
};
const context = {window: {HME: S}, encodeURIComponent, confirm(){ return true; }, console};
vm.runInNewContext(fs.readFileSync('static/js/07-groups.js', 'utf8'), context);
(async () => {
  await S.renderGroups();
  const readonlyCount = (rendered.match(/内置状态分组/g) || []).length;
  if (readonlyCount !== 3) throw new Error('system groups are not readonly: ' + rendered);
  if (!rendered.includes('HME.showGroupModal(&quot;grp_custom&quot;)')) {
    throw new Error('custom group edit action missing: ' + rendered);
  }
})().catch(err => { console.error(err.stack || err.message); process.exit(1); });
'''
    run_node(script)
