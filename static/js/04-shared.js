(function(){
  const S = window.HME;

  S.createShare = async function(alias){
    S.E('modalRoot').innerHTML = '<div class="modal-overlay" onclick="if(event.target===this)HME.closeModal()"><div class="modal-box"><h3><span class="diamond"></span> 生成共享链接</h3><p>持有链接者可公网查看该邮箱最新一封邮件。</p><p class="mono">' + S.esc(alias) + '</p><p class="warning">明文 key 只会在创建成功后显示一次，关闭后无法找回，只能吊销重建。</p><div class="modal-actions"><button class="btn btn-outline" onclick="HME.closeModal()">取消</button><button class="btn" onclick="HME.confirmCreateShare(' + JSON.stringify(alias) + ')">生成</button></div><div id="modalMsg" class="warning"></div></div></div>';
  };

  S.confirmCreateShare = async function(alias){
    try {
      const data = await S.api('/api/mailboxes/' + encodeURIComponent(alias) + '/share', {method:'POST'});
      S.E('modalRoot').innerHTML = '<div class="modal-overlay"><div class="modal-box"><h3><span class="diamond"></span> 共享链接已生成</h3><p class="warning">明文 key 仅此一次显示，关闭后无法找回。</p><input id="shareUrlInput" readonly value="' + S.esc(data.share_url) + '"><div class="modal-actions"><button class="btn btn-outline" onclick="navigator.clipboard.writeText(HME.E(\'shareUrlInput\').value);HME.toast(\'已复制\')">复制链接</button><button class="btn" onclick="HME.closeModal();HME.routeView()">关闭</button></div></div></div>';
    } catch (err) { S.E('modalMsg').textContent = err.message; }
  };

  S.revokeShare = async function(id, alias){
    if (!confirm('确认吊销该共享链接？吊销后公网访问统一返回 404。')) return;
    try {
      await S.api('/api/shared/' + encodeURIComponent(id) + '/revoke', {method:'POST'});
      S.toast('已吊销');
      if (alias) S.renderMailboxDetail(alias); else S.renderShared();
    } catch (err) { S.toast(err.message, true); }
  };

  S.renderShared = async function(){
    S.setTitle('共享管理');
    S.view(S.loading(5));
    try {
      const data = await S.api('/api/shared');
      const rows = data.shared || [];
      if (!rows.length) {
        S.view('<div class="panel"><div class="panel-head">共享管理</div>' + S.empty('暂无共享邮箱，可在单邮箱详情中生成共享链接') + '</div>');
        return;
      }
      const body = rows.map(r => {
        const routeArg = JSON.stringify('#/mailbox/' + encodeURIComponent(r.alias_email));
        const revokeArg = JSON.stringify(r.id);
        return '<tr><td><a class="link" onclick="HME.navigate(' + routeArg + ')">' + S.esc(r.alias_email) + '</a></td><td>' + S.esc(r.prefix) + '</td><td>' + (r.active ? '<span class="badge shared">ACTIVE</span>' : '<span class="badge err">REVOKED</span>') + '</td><td>' + S.esc(r.created_at || '') + '</td><td>' + S.esc(r.last_accessed_at || '') + '</td><td>' + (r.access_count || 0) + '</td><td><button class="btn btn-outline btn-sm" onclick="HME.navigate(' + routeArg + ')">详情</button> ' + (r.active ? '<button class="btn btn-danger btn-sm" onclick="HME.revokeShare(' + revokeArg + ')">吊销</button>' : '') + '</td></tr>';
      }).join('');
      S.view('<div class="panel"><div class="panel-head"><span>共享管理</span><span>' + rows.length + ' total</span></div><div class="table-wrap"><table class="table"><thead><tr><th>邮箱</th><th>prefix</th><th>状态</th><th>创建</th><th>最近访问</th><th>次数</th><th>操作</th></tr></thead><tbody>' + body + '</tbody></table></div></div>');
    } catch (err) {
      S.view(S.error(err, 'HME.renderShared()'));
    }
  };
})();
